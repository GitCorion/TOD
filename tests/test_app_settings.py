import json
import os
from pathlib import Path

import pytest

from catalogo import app_settings
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _config(tmp_path, db=None):
    return Config(
        db_path=db or (tmp_path / "catalogo.db"),
        legacy_db=tmp_path / "assets.db",
        library_root=tmp_path / "library",
        seed_root=tmp_path / "descargas",
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
    )


def _db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def _item(payload, key):
    return next(item for item in payload["settings"] if item["key"] == key)


def test_db_override_wins_over_config(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    app_settings.save_settings(
        db, {"prowlarr_url": "http://db.local:9696/api/v1"}
    )
    loaded = app_settings.load_config(base)
    assert loaded.prowlarr_url == "http://db.local:9696/api/v1"
    assert _item(app_settings.serialize(loaded), "prowlarr_url")["source"] == "db"

    app_settings.save_settings(db, {}, clear=["prowlarr_url"])
    assert app_settings.load_config(base).prowlarr_url == base.prowlarr_url


def test_secret_is_masked_and_blank_does_not_clear(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    app_settings.save_settings(db, {"qbittorrent_pass": "s3cr3t-value"})
    loaded = app_settings.load_config(base)
    assert loaded.qbittorrent_pass == "s3cr3t-value"

    payload = app_settings.serialize(loaded)
    encoded = json.dumps(payload)
    assert "s3cr3t" not in encoded
    item = _item(payload, "qbittorrent_pass")
    assert item["value"] == ""
    assert item["configured"] is True
    assert item["hint"].endswith("alue")

    # Guardar vacío no borra el secreto existente.
    app_settings.save_settings(db, {"qbittorrent_pass": ""})
    assert app_settings.load_config(base).qbittorrent_pass == "s3cr3t-value"

    # Borrado explícito.
    app_settings.save_settings(db, {}, clear=["qbittorrent_pass"])
    assert app_settings.load_config(base).qbittorrent_pass is None
    assert _item(app_settings.serialize(loaded), "qbittorrent_pass")[
        "configured"
    ] is True


def test_validation_rejects_bad_urls_paths_and_ranges(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        app_settings.save_settings(db, {"prowlarr_url": "no-es-una-url"})
    with pytest.raises(ValueError):
        app_settings.save_settings(db, {"catalog_library": "relativa"})
    with pytest.raises(ValueError):
        app_settings.save_settings(db, {"ondemand_max_active": 99})
    with pytest.raises(ValueError):
        app_settings.save_settings(db, {"max_stream_gb": -1})
    with pytest.raises(ValueError):
        app_settings.save_settings(db, {"clave_inventada": "x"})


def test_stream_size_and_roots_conversions(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    app_settings.save_settings(
        db,
        {
            "max_stream_gb": "2.5",
            "strm_roots": f"{tmp_path / 'a'}{os.pathsep}{tmp_path / 'b'}",
            "ondemand_max_active": 2,
        },
    )
    loaded = app_settings.load_config(base)
    assert loaded.max_stream_bytes == int(2.5 * 1024**3)
    assert loaded.max_active_ondemand == 2
    assert loaded.strm_roots == (str(tmp_path / "a"), str(tmp_path / "b"))

    payload = app_settings.serialize(loaded)
    assert _item(payload, "max_stream_gb")["value"] == 2.5
    assert os.pathsep.join(loaded.strm_roots) == _item(
        payload, "strm_roots"
    )["value"]


def test_downloads_root_derives_ondemand_path(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    downloads = tmp_path / "nuevas"
    app_settings.save_settings(db, {"downloads_root": str(downloads)})
    loaded = app_settings.load_config(base)
    assert loaded.downloads_root == downloads
    assert loaded.ondemand_save_path == downloads / "ondemand"


def test_config_load_does_not_create_missing_db(tmp_path):
    missing = tmp_path / "no-existe" / "tod.db"
    config = Config.load(missing)
    assert config.db_path == missing
    assert not missing.exists()
    assert not missing.parent.exists()


def test_path_status_reports_space_and_writability(tmp_path):
    status = app_settings.path_status(tmp_path)
    assert status["exists"] is True
    assert status["is_dir"] is True
    assert status["writable"] is True
    assert status["free"] > 0

    missing = tmp_path / "futuro" / "sub"
    status = app_settings.path_status(missing)
    assert status["exists"] is False
    assert status["checked_parent"] == str(tmp_path)


def test_source_reports_environment(monkeypatch, tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    monkeypatch.setenv("PROWLARR_URL", "http://env.local:9696/api/v1")
    payload = app_settings.serialize(base)
    assert _item(payload, "prowlarr_url")["source"] == "env"
