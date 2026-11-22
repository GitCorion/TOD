import socket
import urllib.error
from pathlib import Path

import pytest

from catalogo import integrations
from catalogo.config import Config

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _config(tmp_path, **overrides):
    dirs = {}
    for name in ("catalogo", "library", "descargas", "descargas/ondemand"):
        path = tmp_path / name
        path.mkdir(parents=True, exist_ok=True)
        dirs[name] = path
    values = dict(
        db_path=tmp_path / "catalogo.db",
        legacy_db=tmp_path / "assets.db",
        library_root=dirs["library"],
        seed_root=dirs["descargas"],
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
        catalog_library=dirs["catalogo"],
        downloads_root=dirs["descargas"],
        ondemand_save_path=dirs["descargas/ondemand"],
    )
    values.update(overrides)
    return Config(**values)


def test_directories_ok(tmp_path):
    result = integrations.test_integration("directories", _config(tmp_path))
    assert result["ok"] is True
    assert set(result["paths"]) == {
        "catalog_library",
        "library_root",
        "seed_root",
        "downloads_root",
        "ondemand_save_path",
    }
    assert result["paths"]["catalog_library"]["writable"] is True


def test_directories_separate_library_roots(tmp_path):
    movies = tmp_path / "fichier/Peliculas"
    series = tmp_path / "fichier/Series"
    movies.mkdir(parents=True)
    series.mkdir(parents=True)
    config = _config(
        tmp_path,
        library_root_movies=movies,
        library_root_series=series,
    )
    result = integrations.test_directories(config)
    assert result["ok"] is True
    assert result["paths"]["library_root_movies"]["exists"] is True
    assert result["paths"]["library_root_series"]["exists"] is True
    assert "library_root" not in result["paths"]


def test_directories_detect_missing(tmp_path):
    config = _config(tmp_path)
    config = Config(**{**config.__dict__, "catalog_library": tmp_path / "no"})
    result = integrations.test_directories(config)
    assert result["ok"] is False
    assert "catalog_library" in result["detail"]


def test_prowlarr_and_qbit_use_clients(monkeypatch, tmp_path):
    class FakeProwlarr:
        @classmethod
        def from_config(cls, config):
            return cls()

        def indexers(self):
            return [{"id": 1}, {"id": 2}]

    class FakeQbit:
        @classmethod
        def from_config(cls, config):
            return cls()

        def app_version(self):
            return "5.0.0"

        def torrents(self):
            return [{"hash": "a"}, {"hash": "b"}, {"hash": "c"}]

    monkeypatch.setattr(integrations, "ProwlarrClient", FakeProwlarr)
    monkeypatch.setattr(integrations, "QbittorrentClient", FakeQbit)
    config = _config(
        tmp_path,
        prowlarr_api_key="x",
        qbittorrent_pass="y",
    )
    prowlarr = integrations.test_prowlarr(config)
    assert prowlarr["ok"] is True
    assert prowlarr["items"] == 2

    qbit = integrations.test_qbittorrent(config)
    assert qbit["ok"] is True
    assert qbit["version"] == "5.0.0"
    assert qbit["items"] == 3


def test_jellyfin_and_jackett_use_http(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        def __init__(self, raw):
            self.raw = raw

        def read(self, size=None):
            return self.raw[:size] if size else self.raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=15.0):
        calls.append((request.full_url, dict(request.header_items())))
        return FakeResponse(b'<?xml version="1.0"?><caps><server title="Jackett"/></caps>')

    def fake_get(url, *, headers=None, timeout=15.0):
        calls.append((url, headers or {}))
        return {"ServerName": "Casa", "Version": "10.9.0"}

    monkeypatch.setattr(integrations.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(integrations, "_get_json", fake_get)
    config = _config(
        tmp_path,
        jackett_api_key="jackett-key",
        jellyfin_api_key="jelly-key",
    )
    jackett = integrations.test_jackett(config)
    assert jackett["ok"] is True
    assert "apikey=jackett-key" in calls[0][0]
    assert "t=caps" in calls[0][0]

    jellyfin = integrations.test_jellyfin(config)
    assert jellyfin["ok"] is True
    assert jellyfin["version"] == "10.9.0"
    assert calls[-1][1]["Authorization"] == 'MediaBrowser Token="jelly-key"'


def test_missing_credentials_are_reported(tmp_path):
    config = _config(tmp_path)
    assert integrations.test_jellyfin(config)["ok"] is False
    assert integrations.test_qbittorrent(config)["ok"] is False
    assert integrations.test_jackett(config)["ok"] is False
    assert "sin API key" in integrations.test_jellyfin(config)["error"]


def test_unknown_integration():
    with pytest.raises(integrations.IntegrationError):
        integrations.test_integration("inventada", None)


def test_error_classification_is_actionable():
    auth = integrations.classify_error(
        urllib.error.HTTPError(
            "http://x", 401, "Unauthorized", {}, None
        )
    )
    assert auth[0] == "auth"
    assert "clave" in auth[1].lower()

    missing = integrations.classify_error(
        integrations.IntegrationError("sin API key de Jellyfin")
    )
    assert missing[0] == "missing"
    assert "Configuración" in missing[1]

    refused = integrations.classify_error(
        urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    )
    assert refused[0] == "refused"
    assert "puerto" in refused[1]

    refused_text = integrations.classify_error(
        integrations.IntegrationError(
            "Prowlarr no responde: [Errno 111] Connection refused"
        )
    )
    assert refused_text[0] == "refused"

    timeout = integrations.classify_error(
        urllib.error.URLError(TimeoutError("timed out"))
    )
    assert timeout[0] == "timeout"

    dns = integrations.classify_error(
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    )
    assert dns[0] == "dns"

    not_api = integrations.classify_error(
        integrations.IntegrationError("respuesta JSON inválida")
    )
    assert not_api[0] == "not_api"


def test_error_text_hides_secrets():
    result = integrations._error(
        "prowlarr",
        0.0,
        integrations.IntegrationError(
            "no responde: http://x/api?apikey=SUPER-SECRETO"
        ),
    )
    assert "SUPER-SECRETO" not in result["error"]
    assert "apikey=***" in result["error"]


def test_light_qbittorrent_skips_torrent_list(monkeypatch, tmp_path):
    class FakeQbit:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_config(cls, config):
            return cls()

        def app_version(self):
            return "5.0.0"

        def torrents(self):
            raise AssertionError("la comprobación ligera no debe listar torrents")

    monkeypatch.setattr(integrations, "QbittorrentClient", FakeQbit)
    result = integrations.test_qbittorrent(
        _config(tmp_path, qbittorrent_pass="x"), light=True
    )
    assert result["ok"] is True
    assert result["detail"] == "qBittorrent 5.0.0"
