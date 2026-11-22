import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Viva", 2024, "COLD"),
    )
    conn.commit()
    conn.close()
    return db


def _config(tmp_path, db):
    for name in ("catalogo", "library", "descargas", "descargas/ondemand"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
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
        downloads_root=tmp_path / "descargas",
        ondemand_save_path=tmp_path / "descargas/ondemand",
    )


def _request(base, path, *, method="GET", body=None, headers=None):
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _server(tmp_path):
    from catalogo.web import make_server

    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    server = make_server("127.0.0.1", 0, config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base


def _item(payload, key):
    return next(item for item in payload["settings"] if item["key"] == key)


def test_settings_read_and_write(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, payload = _request(base, "/api/settings")
        assert status == 200
        assert payload["sections"]
        assert _item(payload, "prowlarr_url")["value"].startswith("http://")
        assert payload["paths"]["catalog_library"]["exists"] is True
        assert "SECRETO" not in json.dumps(payload)

        status, payload = _request(
            base,
            "/api/settings",
            method="POST",
            body={"values": {"prowlarr_url": "http://127.0.0.1:9999/api/v1"}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert "prowlarr_url" in payload["saved"]
        assert server.app.config.prowlarr_url == "http://127.0.0.1:9999/api/v1"
        assert _item(payload, "prowlarr_url")["source"] == "db"

        # La configuración se guardó y sobrevive a una relectura.
        status, payload = _request(base, "/api/settings")
        assert _item(payload, "prowlarr_url")["value"].endswith("9999/api/v1")
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_settings_secret_masked_and_csrf(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, _ = _request(
            base,
            "/api/settings",
            method="POST",
            body={"values": {"qbittorrent_pass": "muy-secreto-123"}},
        )
        assert status == 403

        status, payload = _request(
            base,
            "/api/settings",
            method="POST",
            body={"values": {"qbittorrent_pass": "muy-secreto-123"}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert "muy-secreto-123" not in json.dumps(payload)

        status, payload = _request(base, "/api/settings")
        item = _item(payload, "qbittorrent_pass")
        assert item["configured"] is True
        assert item["value"] == ""
        assert "muy-secreto-123" not in json.dumps(payload)
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_settings_validation_paths_and_integration(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, payload = _request(
            base,
            "/api/settings",
            method="POST",
            body={"values": {"prowlarr_url": "no-es-url"}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 400

        status, payload = _request(
            base,
            "/api/settings/path-check",
            method="POST",
            body={"path": str(tmp_path)},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert payload["exists"] is True
        assert payload["writable"] is True

        status, payload = _request(
            base,
            "/api/integrations/directories/test",
            method="POST",
            body={},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert payload["ok"] is True

        status, payload = _request(
            base,
            "/api/integrations/inventada/test",
            method="POST",
            body={},
            headers={"X-Catalogo": "web"},
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_attention_endpoint(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, payload = _request(base, "/api/attention")
        assert status == 200
        assert payload["counts"]["total"] == len(payload["items"])
        assert any(item["key"] == "setup" for item in payload["items"])

        status, payload = _request(
            base,
            "/api/attention/check",
            method="POST",
            headers={"X-Catalogo": "web"},
        )
        assert status == 202
        assert payload["job_id"] > 0
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_calendar_and_scheduler_params(tmp_path):
    server, base = _server(tmp_path)
    try:
        with sqlite3.connect(server.app.db_path) as conn:
            conn.execute(
                "UPDATE media_items SET release_date = date('now', '+7 days') "
                "WHERE key = 'movie:tmdb:1'"
            )
            conn.commit()

        status, payload = _request(base, "/api/calendar?days=30")
        assert status == 200
        assert [item["key"] for item in payload["items"]] == ["movie:tmdb:1"]

        status, payload = _request(
            base,
            "/api/scheduler/rss-sync",
            method="POST",
            body={"params": {"limit": 42, "delay": 0.5}},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert payload["params"]["limit"] == 42
        assert payload["params"]["delay"] == 0.5
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_release_torrent_download(tmp_path):
    server, base = _server(tmp_path)
    try:
        with sqlite3.connect(server.app.db_path) as conn:
            asset_id = conn.execute(
                "SELECT id FROM media_items LIMIT 1"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
                "source,privacy,torrent_blob) VALUES(?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    "TrackerEspanol",
                    "Viva 1080p",
                    1000,
                    "alive",
                    "prowlarr",
                    "private",
                    b"d4:info4:teste",
                ),
            )
            conn.commit()
        with urllib.request.urlopen(
            base + "/api/releases/1/torrent", timeout=5
        ) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "application/x-bittorrent"
            assert response.read() == b"d4:info4:teste"
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_scheduler_api(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, payload = _request(base, "/api/scheduler")
        assert status == 200
        names = [task["name"] for task in payload["items"]]
        assert "worker-cycle" in names

        status, payload = _request(
            base,
            "/api/scheduler/rss-sync",
            method="POST",
            body={"enabled": False, "interval": 900},
            headers={"X-Catalogo": "web"},
        )
        assert status == 200
        assert payload["enabled"] is False
        assert payload["interval"] == 900

        status, payload = _request(
            base,
            "/api/scheduler/worker-cycle/run",
            method="POST",
            body={},
            headers={"X-Catalogo": "web"},
        )
        assert status == 202
        assert payload["job_id"] >= 1
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()


def test_settings_frontend_assets():
    root = Path(__file__).resolve().parents[1] / "catalogo" / "web_static"
    router = (root / "js/router.js").read_text(encoding="utf-8")
    index = (root / "index.html").read_text(encoding="utf-8")
    view = (root / "js/views/settings.js").read_text(encoding="utf-8")
    app = (root / "js/app.js").read_text(encoding="utf-8")
    assert "renderSettings" in router
    assert "settings: {" in router
    assert 'data-route="settings"' in index
    assert 'data-action="settings-save"' in view
    assert "settings-test" in view
    assert "schedulerRun" not in app
    assert "scheduler-run" in app


def test_unified_server_serves_playback_routes(tmp_path):
    server, base = _server(tmp_path)
    try:
        status, payload = _request(base, "/healthz")
        assert status == 200
        assert payload["status"] == "ok"

        status, payload = _request(base, "/resolve?key=movie:tmdb:1")
        assert status == 200
        assert payload["key"] == "movie:tmdb:1"

        status, payload = _request(base, "/status?key=movie:tmdb:1")
        assert status == 200
        assert payload["key"] == "movie:tmdb:1"

        status, payload = _request(
            base, "/catalog/lookup?path=/tmp/no-existe.strm"
        )
        assert status == 200
        assert payload["is_catalog"] is False
    finally:
        server.shutdown()
        server.server_close()
        server.app.runner.stop()
