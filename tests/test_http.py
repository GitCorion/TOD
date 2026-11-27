import http.client
import json
import sqlite3
import threading
import urllib.parse
from pathlib import Path

from catalogo.db import init_db
from catalogo.http import make_server
from catalogo.identity import play_url
from catalogo.play_service import NoPeers

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def build_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    content = bytes(range(256)) * 4  # 1024 bytes
    local = tmp_path / "local.mkv"
    local.write_bytes(content)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Local", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Local.mkv", len(content), "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,local_path,size_bytes,state) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, release_id, 0, str(local), len(content), "ready"),
    )

    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:2", "movie", 2, "Remoto", 2021),
    )
    asset2 = conn.execute("SELECT id FROM media_items WHERE key='movie:tmdb:2'").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset2, "TrackerEspanol2", "Remoto.mkv", 5000, "alive"),
    )
    conn.commit()
    conn.close()
    return db, content


def test_http_local_range_and_pointer(tmp_path):
    db, content = build_db(tmp_path)
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        def request(method, path, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
            result = (response.status, dict(response.getheaders()), body)
            conn.close()
            return result

        status, headers, body = request("GET", "/healthz")
        assert status == 200
        assert body == b'{"status": "ok"}'

        status, headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A1"
        )
        assert status == 200
        assert len(body) == len(content)
        assert headers["Content-Length"] == str(len(content))
        assert headers["Accept-Ranges"] == "bytes"
        etag = headers["ETag"]

        status, headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A1",
            {"Range": "bytes=0-99"},
        )
        assert status == 206
        assert body == content[:100]
        assert headers["Content-Range"] == f"bytes 0-99/{len(content)}"

        status, headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A1",
            {"Range": "bytes=-100"},
        )
        assert status == 206
        assert body == content[-100:]
        assert headers["Content-Range"] == f"bytes {len(content)-100}-{len(content)-1}/{len(content)}"

        status, headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A1",
            {"Range": f"bytes={len(content)}-"},
        )
        assert status == 416
        assert headers["Content-Range"] == f"bytes */{len(content)}"
        assert body == b""

        status, headers, body = request(
            "HEAD", "/play?key=movie%3Atmdb%3A1"
        )
        assert status == 200
        assert body == b""
        assert headers["Content-Length"] == str(len(content))

        status, headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A1",
            {"If-None-Match": etag},
        )
        assert status == 304
        assert body == b""

        status, headers, body = request(
            "GET", "/resolve?key=movie%3Atmdb%3A1"
        )
        assert status == 200
        assert b'"source": "local_seed"' in body
        assert str(tmp_path).encode() not in body

        status, headers, body = request(
            "GET", "/status?key=movie%3Atmdb%3A1"
        )
        assert status == 200
        assert b'"stream_ready": true' in body

        status, _headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A2"
        )
        assert status == 403
        assert b"download_not_allowed" in body

        status, headers, body = request(
            "HEAD", "/play?key=movie%3Atmdb%3A2"
        )
        assert status == 503
        assert body == b""
        assert int(headers["Content-Length"]) > 0

        status, _headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A2",
            {"Range": "bytes=0-"},
        )
        # Un reproductor con Range puede arrancar el prepare aunque el .strm
        # no lleve download=1. Sin backend, queda en 503 y crea intención.
        assert status == 503
        assert b"backend_disabled" in body
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0] == 1
        conn.close()

        status, _headers, body = request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            {"Range": "bytes=0-"},
        )
        assert status == 503
        assert b"backend_disabled" in body
        conn = sqlite3.connect(db)
        # El intento activo se reutiliza mientras no se consume.
        assert conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0] == 1
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FakePlayService:
    def __init__(self):
        self.calls = []
        self.ensured = []
        self.failed = set()
        self.cleared = []
        self.progress = False

    def is_failed(self, release_id):
        return release_id in self.failed

    def has_progress(self, info_hash):
        return self.progress

    def clear_failed(self, release_id):
        self.cleared.append(release_id)
        self.failed.discard(release_id)

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        self.ensured.append((media_id, release_id))
        return {
            "media_id": media_id,
            "info_hash": "fake",
            "release_id": release_id,
            "file_index": 0,
            "private": False,
        }

    def preflight(self, handle, *, timeout=6.0):
        return None

    def wait_stream_ready(self, handle, *, timeout=60.0):
        self.waits = getattr(self, "waits", [])
        self.waits.append(handle.get("release_id"))
        return True

    def discard_failed(self, handle):
        self.failed.add(handle.get("release_id"))

    def stream_prepared(
        self, handler, handle, start, end, *, head=False, intent_id=None
    ):
        self.calls.append((handle["media_id"], start, end))
        body = b"X" * 100
        handler.send_response(206)
        handler.send_header("Content-Type", "video/x-matroska")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Content-Range", "bytes 0-99/1000")
        handler.end_headers()
        if not head:
            handler.wfile.write(body)

    def stream(self, handler, media_id, start, end, head=False, release_id=None):
        self.calls.append((media_id, start, end))
        body = b"X" * 100
        handler.send_response(206)
        handler.send_header("Content-Type", "video/x-matroska")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Content-Range", "bytes 0-99/1000")
        handler.end_headers()
        if not head:
            handler.wfile.write(body)


def test_cold_range_uses_play_service(tmp_path):
    db, _content = build_db(tmp_path)
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert body == b"X" * 100
        assert service.calls == [("movie:tmdb:2", 0, 99)]
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0] == 1
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FallbackPlayService(FakePlayService):
    def __init__(self, fail_release_id):
        super().__init__()
        self.fail_release_id = fail_release_id
        self.releases = []

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        self.releases.append(release_id)
        return super().ensure(
            media_id, release_id=release_id, torrent_bytes=torrent_bytes,
            force=force,
        )

    def preflight(self, handle, *, timeout=6.0):
        if handle.get("release_id") == self.fail_release_id:
            raise NoPeers("sin pares")


def test_fallback_to_next_release(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    first = conn.execute(
        "SELECT id FROM releases WHERE asset_id=?", (asset_id,)
    ).fetchone()[0]
    second = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Remoto.1080p.mkv", 5000, "alive", 20),
    ).lastrowid
    conn.commit()
    conn.close()
    service = FallbackPlayService(first)
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET",
            f"/play?key=movie%3Atmdb%3A2&release={first}&download=1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert body == b"X" * 100
        assert service.releases == [first, second]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class SlowStartPlayService(FallbackPlayService):
    """La primera fuente no llega a buffer; la segunda sí."""

    def __init__(self, fail_release_id):
        super().__init__(fail_release_id)
        self.waits = []
        self.discarded = []

    def wait_stream_ready(self, handle, *, timeout=60.0):
        self.waits.append((handle.get("release_id"), round(timeout, 1)))
        return handle.get("release_id") != self.fail_release_id

    def discard_failed(self, handle):
        self.discarded.append(handle.get("release_id"))
        super().discard_failed(handle)


def test_slow_first_source_falls_back_in_same_request(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    first = conn.execute(
        "SELECT id FROM releases WHERE asset_id=?", (asset_id,)
    ).fetchone()[0]
    second = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Remoto.1080p.mkv", 5000, "alive", 20),
    ).lastrowid
    conn.commit()
    conn.close()
    service = SlowStartPlayService(first)
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.request(
            "GET",
            f"/play?key=movie%3Atmdb%3A2&release={first}",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert body == b"X" * 100
        assert service.releases == [first, second]
        assert service.discarded == [first]
        assert [release for release, _ in service.waits] == [first, second]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _two_releases(db):
    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    first = conn.execute(
        "SELECT id FROM releases WHERE asset_id=?", (asset_id,)
    ).fetchone()[0]
    second = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Remoto.1080p.mkv", 5000, "alive", 3),
    ).lastrowid
    conn.commit()
    conn.close()
    return asset_id, first, second


def test_prepared_play_waits_for_buffer(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    release_id = conn.execute(
        "SELECT id FROM releases WHERE asset_id=?", (asset_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,state) "
        "VALUES(?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", 5000, "active"),
    )
    conn.commit()
    conn.close()

    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=0",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 206
        assert service.waits == [release_id]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_release_with_progress_is_retried(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
    ).fetchone()[0]
    release_id = conn.execute(
        "SELECT id FROM releases WHERE asset_id=?", (asset_id,)
    ).fetchone()[0]
    conn.close()

    service = FakePlayService()
    service.failed.add(release_id)
    service.progress = True
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 206
        assert service.cleared == [release_id]
        assert service.ensured == [("movie:tmdb:2", release_id)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_candidate_order_avoids_a_source_that_already_failed(tmp_path):
    db, _content = build_db(tmp_path)
    asset_id, first, second = _two_releases(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO swarm_stats(release_id,tracker,connected_seeds,swarm_seeds,"
        "speed_bps,buffer_bytes,required_speed_bps,started_ok,failures,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (first, "TrackerEspanol2 (API)", 2, 20, 1000, 0, 0, 0, 3),
    )
    conn.execute(
        "INSERT INTO swarm_stats(release_id,tracker,connected_seeds,swarm_seeds,"
        "speed_bps,buffer_bytes,required_speed_bps,started_ok,failures,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (second, "TrackerEspanol2", 1, 4, 900000, 0, 0, 1, 0),
    )
    conn.commit()
    conn.close()
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 206
        # A tiene más seeders, pero falló antes: B arranca primero.
        assert service.ensured == [("movie:tmdb:2", second)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_available_copy_wins_over_a_cold_torrent(tmp_path):
    db, content = build_db(tmp_path)
    asset_id, first, second = _two_releases(db)
    local_file = tmp_path / "Local.mkv"
    local_file.write_bytes(b"L" * 300)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress,qbit_state,save_path) VALUES(?,?,?,?,?,?,?,?)",
        ("hash-local", first, "TrackerEspanol2 (API)", "seeding", "ondemand",
         1, "stalledUP", str(tmp_path)),
    )
    torrent_id = conn.execute(
        "SELECT id FROM torrents WHERE info_hash='hash-local'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,size_bytes,state,storage_location,local_path) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (asset_id, first, torrent_id, 0, "Local.mkv", 300, "ready",
         "LOCAL_DISK", str(local_file)),
    )
    conn.commit()
    conn.close()
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=0-299"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert body == b"L" * 300
        assert service.ensured == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DownEnginePlayService(FakePlayService):
    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        from catalogo.play_service import EngineUnavailable

        raise EngineUnavailable("qBittorrent no responde")


def test_engine_down_returns_retryable_503(tmp_path):
    db, _content = build_db(tmp_path)
    service = DownEnginePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        assert response.status == 503
        assert body["error"] == "engine_unavailable"
        assert response.getheader("Retry-After") == "20"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class SeekPlayService(FakePlayService):
    def stream_prepared(
        self, handler, handle, start, end, *, head=False, intent_id=None
    ):
        from catalogo.stream import SeekNotReady

        raise SeekNotReady(start, 0)


def test_far_seek_returns_pending_instead_of_hanging(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE releases SET size_bytes=1000000000")
    conn.commit()
    conn.close()
    service = SeekPlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET",
            "/play?key=movie%3Atmdb%3A2&download=1",
            headers={"Range": "bytes=900000000-900099999"},
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        assert response.status == 503
        assert body["error"] == "seek_not_ready"
        assert response.getheader("X-Catalogo-Seek") == "pending"
        assert response.getheader("Retry-After") == "15"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_starts_without_streaming(tmp_path):
    db, _content = build_db(tmp_path)
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 202
        assert b'"prepared": true' in body
        assert service.ensured == [("movie:tmdb:2", None)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_catalog_lookup_http(tmp_path, monkeypatch):
    db, _content = build_db(tmp_path)
    library = tmp_path / "strm-library"
    library.mkdir()
    path = library / "Local (2020).strm"
    path.write_text(
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1&download=0\n"
    )
    monkeypatch.setenv("CATALOGO_STRM_ROOTS", str(library))
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET",
            "/catalog/lookup?path=" + urllib.parse.quote(str(path), safe=""),
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 200
        assert b'"is_catalog": true' in body
        assert b'"stream_ready": true' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_play_serves_local_file_while_db_is_write_locked(tmp_path):
    db, content = build_db(tmp_path)
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    lock = sqlite3.connect(db)
    try:
        lock.execute("BEGIN IMMEDIATE")
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A1",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert body == content[:100]
    finally:
        lock.rollback()
        lock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_strm_url_starts_download_on_range(tmp_path):
    db, _content = build_db(tmp_path)
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        url = play_url("movie:tmdb:2", f"http://{host}:{port}")
        assert "download=0" in url
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", url.replace(f"http://{host}:{port}", ""),
                     headers={"Range": "bytes=0-99"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 206
        assert service.calls == [("movie:tmdb:2", 0, 99)]
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0] == 1
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sources_endpoint(tmp_path):
    db, _content = build_db(tmp_path)
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/sources?key=movie%3Atmdb%3A1")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 200
        assert b'"sources"' in body
        assert b"download_url" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class NoSourcePlayService(FakePlayService):
    def __init__(self):
        super().__init__()
        from catalogo.backend import NoSource

        self.error = NoSource(
            "sin fuente disponible",
            attempts=[{"release_id": 1, "error": "release_unavailable"}],
        )

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        raise self.error


def test_prepare_returns_sin_fuente(tmp_path):
    db, _content = build_db(tmp_path)
    service = NoSourcePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 503
        assert b'"error": "sin_fuente"' in body
        assert b"release_unavailable" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_lease_endpoint_touch_and_release(tmp_path):
    db, _content = build_db(tmp_path)
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/lease?key=movie%3Atmdb%3A1&session=medusa%3A1",
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 200
        assert b'"leased": true' in body
        conn = sqlite3.connect(db)
        assert conn.execute(
            "SELECT COUNT(*) FROM stream_leases WHERE media_id='movie:tmdb:1'"
        ).fetchone()[0] == 1
        conn.close()

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "DELETE",
            "/lease?key=movie%3Atmdb%3A1&session=medusa%3A1",
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 200
        assert b'"released": true' in body
        conn = sqlite3.connect(db)
        assert conn.execute(
            "SELECT COUNT(*) FROM stream_leases"
        ).fetchone()[0] == 0
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class CapacityPlayService(FakePlayService):
    def __init__(self):
        super().__init__()
        from catalogo.backend import CapacityError

        self.error = CapacityError("ya hay 4 ondemand activos")

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        raise self.error


def test_prepare_capacity_has_message(tmp_path):
    db, _content = build_db(tmp_path)
    service = CapacityPlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 503
        assert b'"error": "capacity"' in body
        assert b'"message":' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class BlockedPlayService(FakePlayService):
    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        from catalogo.play_service import AdmissionDenied

        raise AdmissionDenied({"reason": "tracker_disabled"})


def test_prepare_reports_tracker_blocked(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    release_id, tracker = conn.execute(
        "SELECT id, tracker FROM releases WHERE asset_id="
        "(SELECT id FROM media_items WHERE key='movie:tmdb:2')"
    ).fetchone()
    conn.close()
    service = BlockedPlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            f"/prepare?key=movie%3Atmdb%3A2&release={release_id}",
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        assert response.status == 503
        assert body["error"] == "tracker_blocked"
        assert body["tracker"] == tracker
        assert "bloqueado" in body["message"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FakeArrWrite:
    def __init__(self):
        self.forced = []
        self.resumed = []

    def set_force_start(self, info_hash, value=True):
        self.forced.append((info_hash, value))

    def resume(self, info_hash):
        self.resumed.append(info_hash)


def test_play_adopts_queued_arr_torrent_and_forces_it(tmp_path):
    db, _content = build_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:777", "movie:tmdb:777", "movie", 777, "En cola", 2026,
         "COLD"),
    )
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:777'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,category,state,save_path) "
        "VALUES(?,?,?,?)",
        ("arrhash", "radarr", "waiting", str(tmp_path)),
    )
    torrent_id = conn.execute(
        "SELECT id FROM torrents WHERE info_hash='arrhash'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,torrent_id,file_index,rel_path,"
        "size_bytes,state,storage_location) VALUES(?,?,?,?,?,?,?)",
        (asset_id, torrent_id, 0, "Arr.mkv", 1000, "unknown", "LOCAL_DISK"),
    )
    conn.commit()
    conn.close()

    service = FakePlayService()
    writer = FakeArrWrite()
    service.qbit_write = writer
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET", "/play?key=movie%3Atmdb%3A777",
            headers={"Range": "bytes=0-99"},
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 206
        assert writer.forced == [("arrhash", True)]
        assert writer.resumed == ["arrhash"]
        assert service.calls == [("movie:tmdb:777", 0, 99)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class PinnedBrokenPlayService(FakePlayService):
    """Falla con el release fijado y sirve la mejor viva sin pin."""

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None, force=False):
        from catalogo.backend import ReleaseUnavailable

        if release_id is not None:
            self.ensured.append((media_id, release_id))
            raise ReleaseUnavailable(
                "el índice devolvió HTTP 404",
                release_id=release_id,
                tracker="eMuwarez (API)",
            )
        return super().ensure(media_id, release_id=None, force=force)


def test_prepare_retries_without_pin_when_release_is_broken(tmp_path, monkeypatch):
    from catalogo import http as http_module

    db, _content = build_db(tmp_path)
    service = PinnedBrokenPlayService()
    searched = []

    def fake_search(self, key, *, force=False):
        searched.append((key, force))
        return True

    monkeypatch.setattr(http_module.CatalogHandler, "_search_sources", fake_search)
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        # release 2 existe y está "alive" en el índice, pero abrirlo falla.
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2&release=2")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 202, body
        assert b'"prepared": true' in body
        assert searched == [("movie:tmdb:2", True)]
        assert service.ensured == [("movie:tmdb:2", 2), ("movie:tmdb:2", None)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_with_dead_release_falls_back_to_library_file(tmp_path):
    db, _content = build_db(tmp_path)
    conn_db = sqlite3.connect(db)
    conn_db.execute("UPDATE releases SET health='dead' WHERE id=2")
    conn_db.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,source) VALUES(?,?,?,?,?,?,?)",
        ("movie", 2, 0, 0, "/mnt/fichier/Peliculas/Movie (2024)/Movie.mkv", 123456, "radarr"),
    )
    conn_db.commit()
    conn_db.close()

    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2&release=2")
        response = conn.getresponse()
        body = json.loads(response.read().decode())
        conn.close()
        assert response.status == 202, body
        assert body["already_available"] is True
        assert body["status"]["source"] == "library"
        assert service.ensured == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_prefers_compatible_library_over_torrent_when_caps_passed(tmp_path):
    db, _content = build_db(tmp_path)
    conn_db = sqlite3.connect(db)
    conn_db.execute("UPDATE releases SET seeders=5 WHERE id=2")
    conn_db.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,source) VALUES(?,?,?,?,?,?,?)",
        ("movie", 2, 0, 0, "/mnt/fichier/Peliculas/Movie (2024)/Movie WEBDL-1080p.mkv", 123456, "radarr"),
    )
    conn_db.commit()
    conn_db.close()

    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2&max_width=1340&supports_4k=false&supports_dv=false")
        response = conn.getresponse()
        body = json.loads(response.read().decode())
        conn.close()
        assert response.status == 202, body
        assert body["already_available"] is True
        assert body["status"]["source"] == "library"
        assert service.ensured == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_skips_incompatible_4k_library_and_picks_compatible_torrent(tmp_path):
    db, _content = build_db(tmp_path)
    conn_db = sqlite3.connect(db)
    conn_db.execute("UPDATE releases SET seeders=5 WHERE id=2")
    conn_db.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,source) VALUES(?,?,?,?,?,?,?)",
        ("movie", 2, 0, 0, "/mnt/fichier/Peliculas/Movie (2024)/Movie 2160p DoVi.mkv", 999999, "radarr"),
    )
    conn_db.commit()
    conn_db.close()

    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2&max_width=1340&supports_4k=false&supports_dv=false")
        response = conn.getresponse()
        body = json.loads(response.read().decode())
        conn.close()
        assert response.status == 202, body
        assert body.get("already_available") is not True
        assert service.ensured == [("movie:tmdb:2", 2)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_primary_retries_with_search_when_release_fails(tmp_path, monkeypatch):
    from catalogo import http as http_module
    from catalogo.backend import ReleaseUnavailable

    class PrimaryBrokenPlayService(FakePlayService):
        def ensure(self, media_id, release_id=None, force=False, **kwargs):
            self.ensured.append((media_id, release_id))
            if len(self.ensured) == 1:
                raise ReleaseUnavailable("tracker 404")
            return {
                "info_hash": "hash2",
                "file_index": 0,
                "release_id": 2,
                "tracker": "eMuwarez (API)",
            }

    db, _content = build_db(tmp_path)
    service = PrimaryBrokenPlayService()
    searched = []

    def fake_search(self, key, *, force=False):
        searched.append((key, force))
        return True

    monkeypatch.setattr(http_module.CatalogHandler, "_search_sources", fake_search)
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/prepare?key=movie%3Atmdb%3A2")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 202, body
        assert searched == [("movie:tmdb:2", True)]
        assert service.ensured == [
            ("movie:tmdb:2", None),
            ("movie:tmdb:2", None),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_play_torrent_triggers_async_source_refresh_and_skips_local(tmp_path, monkeypatch):
    from catalogo import http as http_module

    db, _content = build_db(tmp_path)
    conn_db = sqlite3.connect(db)
    conn_db.execute("UPDATE releases SET seeders=5 WHERE id=2")
    conn_db.commit()
    conn_db.close()

    async_calls = []

    def fake_async(self, key):
        async_calls.append(key)

    monkeypatch.setattr(http_module.CatalogHandler, "_search_sources_async", fake_async)
    service = FakePlayService()
    server = make_server("127.0.0.1", 0, db, play_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        # Local seed -> NO debe disparar refresco asíncrono.
        conn.request("GET", "/play?key=movie%3Atmdb%3A1", headers={"Range": "bytes=0-3"})
        r1 = conn.getresponse()
        r1.read()
        assert r1.status == 206
        assert async_calls == []

        # Torrent frío con fuente viva -> SÍ debe disparar refresco asíncrono.
        conn.request("GET", "/play?key=movie%3Atmdb%3A2", headers={"Range": "bytes=0-3"})
        r2 = conn.getresponse()
        r2.read()
        conn.close()
        assert r2.status == 206
        assert async_calls == ["movie:tmdb:2"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
