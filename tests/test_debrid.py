import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from catalogo.db import init_db
from catalogo.debrid import DebridCache, RealDebridClient
from catalogo.debrid_cache import infohash_for_release, cache_public

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeRD(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    delete_calls = 0
    selected = False

    def log_message(self, *args):
        return

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/torrents/addMagnet":
            self._json({"id": "T1", "uri": "x"})
        elif self.path == "/torrents/selectFiles/T1":
            FakeRD.selected = True
            self.send_response(204)
            self.end_headers()
        elif self.path == "/unrestrict/link":
            self._json({"download": "https://direct/file.mkv"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/torrents/info/T1":
            if FakeRD.selected:
                self._json({"status": "downloaded", "links": ["https://rd/link"]})
            else:
                self._json({"status": "waiting_files_selection"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        FakeRD.delete_calls += 1
        self.send_response(204)
        self.end_headers()


def test_rd_add_and_check_cached():
    FakeRD.delete_calls = 0
    FakeRD.selected = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRD)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = RealDebridClient(
            "key", base_url=f"http://{host}:{port}", min_interval=0
        )
        result = client.check_torrent("abc", timeout=2)
        assert result.cached
        assert result.link == "https://direct/file.mkv"
        assert FakeRD.delete_calls == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FakeCacheClient:
    def __init__(self):
        self.hashes = []

    def check_torrent(self, infohash):
        self.hashes.append(infohash)
        return DebridCache(True, "https://direct/x.mkv", "T1", "downloaded")


def test_cache_public_only_public(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Public"),
    )
    asset = conn.execute("SELECT id FROM media_items").fetchone()[0]
    blob = b"d4:info4:teste"
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,privacy,source,torrent_blob) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset, "Publico1", "Public.mkv", 1000, "public", "jackett", blob),
    )
    public_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,privacy,source,torrent_blob) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset, "TrackerEspanol (API)", "Private.mkv", 1000, "private", "prowlarr", blob),
    )
    private_id = conn.execute(
        "SELECT id FROM releases WHERE privacy='private'"
    ).fetchone()[0]
    conn.commit()
    conn.close()

    client = FakeCacheClient()
    report = cache_public(str(db), client, delay=0)
    assert report["checked"] == 1
    assert report["cached"] == 1
    assert len(client.hashes) == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    pub = conn.execute("SELECT * FROM releases WHERE id=?", (public_id,)).fetchone()
    priv = conn.execute("SELECT * FROM releases WHERE id=?", (private_id,)).fetchone()
    assert pub["debrid_cached"] == 1
    assert pub["debrid_link"] == "https://direct/x.mkv"
    assert priv["debrid_cached"] is None
    assert priv["debrid_link"] is None
    conn.close()
