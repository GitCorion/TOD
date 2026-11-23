from pathlib import Path

import pytest

from catalogo.config import Config
from catalogo.prowlarr import ProwlarrClient, ProwlarrError

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _config(tmp_path, *, api_key):
    return Config(
        db_path=tmp_path / "db.sqlite",
        legacy_db=tmp_path / "legacy.sqlite",
        library_root=tmp_path,
        seed_root=tmp_path,
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:9696/api/v1",
        prowlarr_api_key=api_key,
        qbittorrent_url="http://127.0.0.1:1340",
        qbittorrent_user="qbittorrent",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:9117/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=20 * 1024**3,
    )


def test_client_requires_key(tmp_path):
    with pytest.raises(ProwlarrError, match="API key"):
        ProwlarrClient("http://127.0.0.1:9696/api/v1", "")
    with pytest.raises(ProwlarrError, match="Configuración"):
        ProwlarrClient.from_config(_config(tmp_path, api_key=None))


def test_client_from_config_uses_api_key(tmp_path):
    config = _config(tmp_path, api_key="abc123")
    client = ProwlarrClient.from_config(config)
    assert client.api_key == "abc123"
    assert client.base_url == "http://127.0.0.1:9696/api/v1"


def test_client_does_not_read_config_xml(tmp_path):
    config_xml = tmp_path / "config.xml"
    config_xml.write_text("<Config><ApiKey>abc123</ApiKey></Config>")
    config = _config(tmp_path, api_key=None)
    with pytest.raises(ProwlarrError):
        ProwlarrClient.from_config(config)


def test_torznab_base_strips_api_v1(tmp_path):
    client = ProwlarrClient("http://127.0.0.1:9696/api/v1", "abc")
    assert client.torznab_base == "http://127.0.0.1:9696"


def test_torznab_search_sends_ids_and_parses_attrs(monkeypatch):
    xml = b"""<?xml version="1.0"?>
    <rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
      <item>
        <title>Dune Parte dos (2024) [HDO].mkv SPANiSH</title>
        <guid>guid-1</guid>
        <link>http://127.0.0.1:9696/1/download?apikey=x</link>
        <comments>https://hd-olimpo.club/torrents/1</comments>
        <size>1234</size>
        <category>2000</category>
        <torznab:attr name="seeders" value="7"/>
        <torznab:attr name="peers" value="2"/>
        <torznab:attr name="infohash" value="ABCDEF"/>
        <torznab:attr name="tmdbid" value="693134"/>
      </item>
    </channel></rss>"""

    class FakeResponse:
        def __init__(self, raw):
            self._raw = raw

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        return FakeResponse(xml)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ProwlarrClient("http://127.0.0.1:9696/api/v1", "abc")
    results = client.torznab_search(1, media_type="movie", tmdb_id=693134)
    assert "/1/api?" in captured["url"]
    assert "tmdbid=693134" in captured["url"]
    assert "t=movie" in captured["url"]
    assert results == [
        {
            "title": "Dune Parte dos (2024) [HDO].mkv SPANiSH",
            "size": 1234,
            "seeders": 7,
            "peers": 2,
            "infoHash": "abcdef",
            "downloadUrl": "http://127.0.0.1:9696/1/download?apikey=x",
            "infoUrl": "https://hd-olimpo.club/torrents/1",
            "guid": "guid-1",
            "tmdbId": 693134,
            "imdbId": 0,
            "category": "2000",
        }
    ]
