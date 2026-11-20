import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from catalogo.qbit_write import QbittorrentWriteClient


class FakeQbit(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_add = b""

    def log_message(self, *args):
        return

    def _ok(self, body=b"Ok."):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/api/v2/auth/login":
            self.send_response(200)
            self.send_header("Set-Cookie", "SID=test; path=/")
            self.send_header("Content-Length", "3")
            self.end_headers()
            self.wfile.write(b"Ok.")
            return
        if self.path == "/api/v2/torrents/add":
            FakeQbit.last_add = body
            self._ok()
            return
        if self.path in (
            "/api/v2/torrents/filePrio",
            "/api/v2/torrents/setForceStart",
            "/api/v2/torrents/start",
            "/api/v2/torrents/stop",
            "/api/v2/torrents/delete",
        ):
            self._ok()
            return
        self.send_response(404)
        self.end_headers()


def test_add_torrent_paused_and_priorities():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQbit)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = QbittorrentWriteClient(
            f"http://{host}:{port}", "user", "correct"
        )
        client.add_torrent(
            b"d4:info4:teste",
            save_path="/mnt/multimedia/ondemand",
            category="ondemand",
            paused=True,
        )
        body = FakeQbit.last_add
        assert b'name="paused"\r\n\r\ntrue' in body
        assert b'name="category"\r\n\r\nondemand' in body
        assert b'filename="catalogo.torrent"' in body
        assert b'name="sequentialDownload"\r\n\r\ntrue' in body
        assert b'name="firstLastPiecePrio"\r\n\r\ntrue' in body

        client.set_file_priorities("abc", {0: 1, 1: 7})
        client.set_force_start("abc", True)
        client.resume("abc")
        client.pause("abc")
        client.delete("abc", delete_files=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
