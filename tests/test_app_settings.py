import json
import os
from pathlib import Path

import pytest

from catalogo import app_settings
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


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
    # Las rutas .strm se calculan solas: no aparecen como campo en la web.
    assert "strm_roots" not in {item["key"] for item in payload["settings"]}
    assert "library_root" not in {item["key"] for item in payload["settings"]}
    assert "seed_root" not in {item["key"] for item in payload["settings"]}


def test_downloads_root_derives_ondemand_path(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    downloads = tmp_path / "nuevas"
    app_settings.save_settings(db, {"downloads_root": str(downloads)})
    loaded = app_settings.load_config(base)
    assert loaded.downloads_root == downloads
    assert loaded.ondemand_save_path == downloads / "ondemand"


def test_move_target_setting_and_destination(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    assert base.move_target is None
    assert base.move_destination() == tmp_path / "library" / "descargas"

    target = tmp_path / "archivo"
    app_settings.save_settings(db, {"move_target": str(target)})
    loaded = app_settings.load_config(base)
    assert loaded.move_target == target
    assert loaded.move_destination() == target
    assert target in loaded.archive_roots()
    assert _item(app_settings.serialize(loaded), "move_target")["value"] == str(
        target
    )

    empty = _item(app_settings.serialize(base), "move_target")
    assert empty["value"] == ""
    assert empty["effective"] == str(tmp_path / "library" / "descargas")

    app_settings.save_settings(db, {}, clear=["move_target"])
    restored = app_settings.load_config(base)
    assert restored.move_target is None
    assert restored.move_destination() == tmp_path / "library" / "descargas"


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


def test_tod_env_aliases(monkeypatch, tmp_path):
    monkeypatch.setenv("TOD_LIBRARY_ROOT", str(tmp_path / "library"))
    monkeypatch.setenv("TOD_MOVE_TARGET", str(tmp_path / "archivo"))
    monkeypatch.setenv("TOD_WEB_TOKEN", "token-tod")
    monkeypatch.setenv("TOD_DB", str(tmp_path / "tod.db"))
    config = Config.from_env()
    assert config.library_root == tmp_path / "library"
    assert config.move_target == tmp_path / "archivo"
    assert config.web_token == "token-tod"
    assert config.db_path == tmp_path / "tod.db"


def test_source_reports_environment(monkeypatch, tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    monkeypatch.setenv("PROWLARR_URL", "http://env.local:9696/api/v1")
    payload = app_settings.serialize(base)
    assert _item(payload, "prowlarr_url")["source"] == "env"


def test_paths_derived_from_library_and_downloads(tmp_path):
    db = _db(tmp_path)
    base = _config(tmp_path, db)
    movies = tmp_path / "fichier" / "Peliculas"
    series = tmp_path / "fichier" / "Series"
    app_settings.save_settings(
        db,
        {
            "catalog_library": str(tmp_path / "accesos"),
            "library_root_movies": str(movies),
            "library_root_series": str(series),
            "downloads_root": str(tmp_path / "descargas"),
        },
    )
    loaded = app_settings.load_config(base)
    assert loaded.strm_roots == (str(tmp_path / "accesos"),)
    assert loaded.effective_library_root() == tmp_path / "fichier"
    assert loaded.move_destination() == tmp_path / "fichier" / "descargas"
    assert loaded.seed_root == tmp_path / "descargas" / "catalogo"


def test_from_env_derives_strm_roots_and_seed_root(monkeypatch, tmp_path):
    for key in (
        "CATALOGO_STRM_ROOTS", "TOD_STRM_ROOTS",
        "CATALOGO_SEED_ROOT", "TOD_SEED_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOD_CATALOG_LIBRARY", str(tmp_path / "accesos"))
    monkeypatch.setenv("TOD_DOWNLOADS_ROOT", str(tmp_path / "descargas"))
    config = Config.from_env()
    assert config.strm_roots == (str(tmp_path / "accesos"),)
    assert config.seed_root == tmp_path / "descargas" / "catalogo"

    monkeypatch.setenv("TOD_STRM_ROOTS", str(tmp_path / "otras"))
    assert Config.from_env().strm_roots == (str(tmp_path / "otras"),)
