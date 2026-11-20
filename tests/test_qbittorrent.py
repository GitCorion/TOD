import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from catalogo.qbittorrent import QbittorrentClient, QbittorrentError


class FakeQbit(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        body = self.rfile.read(length).decode()
        if self.path == "/api/v2/auth/login":
            if "password=correct" in body:
                self.send_response(200)
                self.send_header("Set-Cookie", "SID=test-cookie; path=/")
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"Ok.")
            else:
                self.send_response(200)
                self.send_header("Content-Length", "6")
                self.end_headers()
                self.wfile.write(b"Fails.")
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.headers.get("Cookie") != "SID=test-cookie":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/api/v2/app/version":
            body = b"v5.1.4"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/v2/torrents/info"):
            self._json([{"hash": "abc", "name": "Test", "state": "pausedUP"}])
        elif self.path.startswith("/api/v2/torrents/files"):
            self._json([{"index": 0, "name": "Test.mkv", "size": 1000}])
        elif self.path.startswith("/api/v2/torrents/properties"):
            self._json({"piece_size": 262144, "pieces_num": 4})
        elif self.path.startswith("/api/v2/torrents/pieceStates"):
            self._json([2, 2, 2, 2])
        elif self.path.startswith("/api/v2/torrents/trackers"):
            self._json([{"url": "https://tracker"}])
        else:
            self.send_response(404)
            self.end_headers()


def test_read_only_client(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQbit)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = QbittorrentClient(
            f"http://{host}:{port}", "user", "correct"
        )
        assert client.app_version() == "v5.1.4"
        torrents = client.torrents()
        assert torrents[0]["hash"] == "abc"
        assert client.files("abc")[0]["name"] == "Test.mkv"
        assert client.properties("abc")["piece_size"] == 262144
        assert client.piece_states("abc") == [2, 2, 2, 2]
        assert client.trackers("abc")[0]["url"] == "https://tracker"
        # No debe existir ningún método mutante.
        for name in ("add_torrent", "delete", "pause", "resume"):
            assert not hasattr(client, name)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_relogin_on_expired_session(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQbit)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = QbittorrentClient(
            f"http://{host}:{port}", "user", "correct"
        )
        client.login()
        client.jar.clear()
        # Simula cookie caducada: el cliente cree que sigue logueado.
        client._logged_in = True
        assert client.files("abc")[0]["name"] == "Test.mkv"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_login_rejected(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQbit)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = QbittorrentClient(
            f"http://{host}:{port}", "user", "wrong"
        )
        try:
            client.login()
        except QbittorrentError as exc:
            assert "rechazado" in str(exc)
        else:
            raise AssertionError("debía rechazar el login")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_requires_password():
    try:
        QbittorrentClient("http://127.0.0.1:1340", "user", None)
    except QbittorrentError as exc:
        assert "contraseña" in str(exc)
    else:
        raise AssertionError("debía exigir contraseña")
