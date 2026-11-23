import http.client
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

    def is_failed(self, release_id):
        return release_id in self.failed

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None):
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
        return True

    def discard_failed(self, handle):
        self.failed.add(handle.get("release_id"))

    def stream_prepared(self, handler, handle, start, end, *, head=False):
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

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None):
        self.releases.append(release_id)
        return super().ensure(
            media_id, release_id=release_id, torrent_bytes=torrent_bytes
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

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None):
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

    def ensure(self, media_id, *, release_id=None, torrent_bytes=None):
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
