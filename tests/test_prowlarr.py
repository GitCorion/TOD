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
        rd_api_key=None,
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
