import json
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from catalogo import admin
from catalogo.config import Config
from catalogo.db import init_db
from catalogo.jobs import JobConflict, JobRunner, get_job, list_jobs, recover_jobs
from catalogo.queries import list_titles, overview, title_detail

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Viva", 2024, "SEEDING_LOCAL"),
    )
    movie_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,seeders,health,"
        "source,privacy,download_url,info_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (movie_id, "Tracker", "Viva 1080p", 1000, 20, "alive",
         "prowlarr", "private", "https://tracker/download/SECRETO", "hash1"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
        "size_bytes,state,storage_location,local_path) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (movie_id, release_id, 0, "Viva.mkv", 1000, "ready",
         "LOCAL_DISK", str(tmp_path / "Viva.mkv")),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,state) "
        "VALUES(?,?,?,?,?)",
        (movie_id, release_id, "Tracker", 1000, "seeding"),
    )
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:2", "movie:tmdb:2", "movie", 2, "Sin verificar", 2023, "COLD"),
    )
    other_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,seeders,health,"
        "source,privacy) VALUES(?,?,?,?,?,?,?,?)",
        (other_id, "Tracker", "Sin verificar 720p", 500, 3, "unknown",
         "prowlarr", "private"),
    )
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:3", "movie:tmdb:3", "movie", 3, "Vacía", 2022, "COLD"),
    )
    conn.commit()
    conn.close()
    return db, movie_id, release_id


def config_for(tmp_path, db, token=None):
    return Config(
        db_path=db,
        legacy_db=db,
        library_root=tmp_path / "library",
        seed_root=tmp_path / "descargas",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:1/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1",
        qbittorrent_user="x",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:1/api/v2.0",
        jackett_api_key=None,
        rd_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=tmp_path / "catalogo",
        web_token=token,
    )


def test_list_titles_availability_and_secret(tmp_path):
    db, movie_id, _ = seed(tmp_path)
    data = list_titles(db)
    assert data["total"] == 3
    by_title = {item["title"]: item for item in data["items"]}
    assert by_title["Viva"]["availability"] == "local"
    assert by_title["Sin verificar"]["availability"] == "unverified"
    assert by_title["Vacía"]["availability"] == "none"

    unverified = list_titles(db, availability="unverified")
    assert [item["title"] for item in unverified["items"]] == ["Sin verificar"]
    none = list_titles(db, availability="none")
    assert [item["title"] for item in none["items"]] == ["Vacía"]

    detail = title_detail(db, movie_id)
    assert detail is not None
    assert "download_url" not in detail["releases"][0]
    encoded = json.dumps(detail)
    assert "SECRETO" not in encoded
    assert detail["files"][0]["storage_location"] == "LOCAL_DISK"


def test_overview_counts(tmp_path):
    db, _, _ = seed(tmp_path)
    data = overview(db)
    assert data["media"]["total"] == 3
    assert data["media"]["with_releases"] == 2
    assert data["releases"]["alive"] == 1
    assert data["releases"]["unknown"] == 1
    assert data["files"]["local"] == 1
    assert data["commitments"]["active"] == 1
    assert data["jobs"]["running"] == 0


def test_jobs_runner_roundtrip(tmp_path):
    db = tmp_path / "jobs.db"
    init_db(db, MIGRATIONS)
    seen = {}
    release = threading.Event()

    def handler(ctx):
        ctx.report("mitad", progress=0.5)
        seen["id"] = ctx.job_id
        release.wait(5)
        return {"ok": True}

    runner = JobRunner(db, object(), {"dummy": handler})
    runner.start()
    try:
        job_id = runner.submit("dummy", requested_by="test")
        with pytest.raises(JobConflict):
            runner.submit("dummy")
        release.set()
        deadline = time.monotonic() + 5
        job = None
        while time.monotonic() < deadline:
            job = get_job(db, job_id)
            if job["state"] in ("ok", "error"):
                break
            time.sleep(0.02)
        assert job is not None
        assert job["state"] == "ok"
        assert job["result"] == {"ok": True}
        assert seen["id"] == job_id
        assert job["finished_at"]
        assert list_jobs(db)[0]["kind"] == "dummy"
    finally:
        release.set()
        runner.stop()


def test_recover_jobs_marks_running_as_cancelled(tmp_path):
    db = tmp_path / "recover.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs(kind, state, started_at) VALUES('x','running','now')"
    )
    conn.commit()
    conn.close()
    assert recover_jobs(db) == 1
    job = list_jobs(db)[0]
    assert job["state"] == "cancelled"
    assert "reinicio" in job["error"]


def test_delete_is_left_to_qbittorrent(tmp_path):
    db, _, _ = seed(tmp_path)
    config = config_for(tmp_path, db)
    with pytest.raises(Exception, match="qBittorrent"):
        admin.qbit_action(
            config, "hash1", "delete", delete_files=True
        )


def _request(base, path, *, method="GET", body=None, headers=None, token=None):
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Cookie"] = f"catalogo_web={token}"
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_web_endpoints_local_and_csrf(tmp_path):
    db, _movie_id, _ = seed(tmp_path)
    config = config_for(tmp_path, db)
    from catalogo.web import make_server

    server = make_server("127.0.0.1", 0, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(base, "/api/session")
        assert status == 200
        assert payload["authenticated"] is True

        status, payload = _request(base, "/api/overview")
        assert status == 200
        assert payload["media"]["total"] == 3

        status, payload = _request(base, "/api/titles/1")
        assert status == 200
        assert payload["item"]["title"] == "Viva"
        assert "SECRETO" not in json.dumps(payload)

        # Mutación sin la cabecera anti-CSRF: rechazada.
        status, payload = _request(
            base, "/api/jobs", method="POST",
            body={"kind": "cleanup-leases", "params": {}},
        )
        assert status == 403

        status, payload = _request(
            base, "/api/jobs", method="POST",
            body={"kind": "cleanup-leases", "params": {}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 202
        assert payload["job_id"] >= 1

        # Host ajeno (DNS rebinding) rechazado.
        status, payload = _request(
            base, "/api/overview", headers={"Host": "evil.example:80"}
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_web_allows_docker_internal_host(tmp_path):
    """Jellyfin llama a TOD como http://tod:8773 dentro de Compose."""
    from dataclasses import replace

    from catalogo.web import make_server

    db, _, _ = seed(tmp_path)
    config = replace(config_for(tmp_path, db), web_allowed_hosts="tod")
    server = make_server("127.0.0.1", 0, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        status, payload = _request(
            base, "/api/overview", headers={"Host": f"tod:{port}"}
        )
        assert status == 200
        assert payload["media"]["total"] == 3
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_title_api_returns_stored_episode_metadata(tmp_path):
    from catalogo.web import make_server

    db, _movie_id, _ = seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,season,"
            "episode,status,episode_title,episode_overview) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "series:tmdb:77:1:2",
                "series:tmdb:77:1:2",
                "series",
                77,
                "Serie",
                2024,
                1,
                2,
                "COLD",
                "Las normas de la tienda",
                "Sinopsis del episodio",
            ),
        )
        conn.commit()
        episode_id = conn.execute(
            "SELECT id FROM media_items WHERE key='series:tmdb:77:1:2'"
        ).fetchone()[0]

    config = config_for(tmp_path, db)
    server = make_server("127.0.0.1", 0, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(base, f"/api/titles/{episode_id}")
        assert status == 200
        assert payload["item"]["episode_title"] == "Las normas de la tienda"
        assert payload["item"]["episode_overview"] == "Sinopsis del episodio"
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_runtime_detection(monkeypatch):
    from catalogo import web

    monkeypatch.setenv("TOD_RUNTIME", " Docker ")
    assert web._runtime() == "docker"
    monkeypatch.setenv("TOD_RUNTIME", "systemd")
    assert web._runtime() == "systemd"


def test_web_token_required(tmp_path):
    from catalogo.web import _make_cookie, _valid_cookie, make_server

    assert _valid_cookie("secreto", _make_cookie("secreto"))
    assert not _valid_cookie("secreto", "123.firma")

    db, _, _ = seed(tmp_path)
    config = config_for(tmp_path, db, token="secreto")
    server = make_server("127.0.0.1", 0, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(base, "/api/session")
        assert status == 200
        assert payload["auth_required"] is True
        assert payload["authenticated"] is False

        status, _ = _request(base, "/api/overview")
        assert status == 401

        status, payload = _request(
            base, "/api/login", method="POST",
            body={"token": "secreto"},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        # La cookie se envía mediante Set-Cookie; probamos su firma.
        cookie = _make_cookie("secreto")
        status, payload = _request(
            base, "/api/overview", token=cookie
        )
        assert status == 200
        assert payload["media"]["total"] == 3

        status, _ = _request(
            base, "/api/jobs", method="POST",
            body={"kind": "cleanup-leases", "params": {}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()
