import json
from pathlib import Path

from catalogo.config import Config
from catalogo.jellyfin import notify_updated

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _config(tmp_path, *, api_key="jelly-key"):
    return Config(
        db_path=tmp_path / "catalogo.db",
        legacy_db=tmp_path / "assets.db",
        library_root=tmp_path / "library",
        seed_root=tmp_path / "seed",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:9696/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1340",
        qbittorrent_user="qbittorrent",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:9117/api/v2.0",
        jackett_api_key=None,
        rd_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=tmp_path / "catalogo",
        jellyfin_url="http://127.0.0.1:8086",
        jellyfin_api_key=api_key,
    )


def test_notify_updated_posts_paths(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=20):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(
        "catalogo.jellyfin.urllib.request.urlopen", fake_urlopen
    )
    path = tmp_path / "catalogo" / "Serie" / "S01E01.strm"
    result = notify_updated(_config(tmp_path), [path])
    assert result["ok"] is True
    assert result["paths"] == 1
    assert captured["url"].endswith("/Library/Media/Updated")
    assert captured["payload"] == {
        "Updates": [{"Path": str(path), "UpdateType": "Created"}]
    }
    assert captured["headers"]["Authorization"] == 'MediaBrowser Token="jelly-key"'


def test_notify_without_key_is_noop(tmp_path):
    result = notify_updated(_config(tmp_path, api_key=None), [tmp_path / "x.strm"])
    assert result == {"ok": False, "reason": "sin_api_key"}
