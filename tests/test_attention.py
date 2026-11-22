import sqlite3
from pathlib import Path

from catalogo import attention, integrations
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _config(tmp_path, **overrides):
    for name in ("catalogo", "library", "descargas", "descargas/ondemand"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    values = dict(
        db_path=tmp_path / "catalogo.db",
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
        downloads_root=tmp_path / "descargas",
        ondemand_save_path=tmp_path / "descargas/ondemand",
    )
    values.update(overrides)
    return Config(**values)


def _db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def _keys(items):
    return {item.key for item in items}


def test_missing_setup_is_one_item(tmp_path):
    config = _config(tmp_path)
    items = attention.collect(_db(tmp_path), config)
    setup = next(item for item in items if item.key == "setup")
    assert setup.severity == "fail"
    assert "qBittorrent" in setup.detail
    assert "Prowlarr o Jackett" in setup.detail
    assert setup.href.startswith("#/settings")


def test_missing_paths_and_downloads(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO torrents(info_hash, state) VALUES ('a', 'paused')"
        )
        conn.execute(
            "INSERT INTO torrents(info_hash, state) VALUES ('b', 'error')"
        )
        conn.commit()
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jellyfin_api_key="x",
        tmdb_api_key="x",
        catalog_library=tmp_path / "no-existe",
    )
    items = attention.collect(db, config)
    keys = _keys(items)
    assert "downloads:paused" in keys
    assert "downloads:error" in keys
    assert "path:" + str(tmp_path / "no-existe") in keys


def test_separate_library_roots_replace_common_root(tmp_path):
    db = _db(tmp_path)
    movies = tmp_path / "fichier/Peliculas"
    series = tmp_path / "fichier/Series"
    movies.mkdir(parents=True)
    series.mkdir(parents=True)
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jellyfin_api_key="x",
        tmdb_api_key="x",
        library_root=tmp_path / "library-que-no-existe",
        library_root_movies=movies,
        library_root_series=series,
    )
    keys = _keys(attention.collect(db, config))
    assert "path:" + str(movies) not in keys
    assert "path:" + str(series) not in keys
    assert "path:" + str(tmp_path / "library-que-no-existe") not in keys


def test_integration_health_needs_two_failures(tmp_path):
    db = _db(tmp_path)
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jellyfin_api_key="x",
        tmdb_api_key="x",
    )
    failure = {
        "ok": False,
        "error": "HTTP 401",
        "kind": "auth",
        "hint": "La clave no es válida.",
    }
    attention.record(db, "prowlarr", failure)
    assert "integration:prowlarr" not in _keys(attention.collect(db, config))

    attention.record(db, "prowlarr", failure)
    item = next(
        item
        for item in attention.collect(db, config)
        if item.key == "integration:prowlarr"
    )
    assert item.severity == "fail"
    assert item.hint == "La clave no es válida."
    assert item.since

    attention.record(db, "prowlarr", {"ok": True, "detail": "ok"})
    assert "integration:prowlarr" not in _keys(attention.collect(db, config))


def test_prowlarr_failure_is_warning_when_jackett_works(tmp_path):
    db = _db(tmp_path)
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jackett_api_key="y",
        jellyfin_api_key="x",
        tmdb_api_key="x",
    )
    failure = {"ok": False, "error": "no responde", "hint": "Revisa la URL."}
    attention.record(db, "prowlarr", failure)
    attention.record(db, "prowlarr", failure)
    item = next(
        item
        for item in attention.collect(db, config)
        if item.key == "integration:prowlarr"
    )
    assert item.severity == "warn"


def test_run_checks_stores_results(monkeypatch, tmp_path):
    db = _db(tmp_path)
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jellyfin_api_key="x",
        tmdb_api_key="x",
    )

    def fake_test(name, config, *, light=False):
        assert light is True
        if name == "tmdb":
            return {"ok": False, "error": "HTTP 401", "kind": "auth", "hint": "Clave inválida."}
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr(integrations, "test_integration", fake_test)
    report = attention.run_checks(db, config)
    assert report["checked"] == 4
    assert report["failing"] == 0  # tmdb no es imprescindible para el flujo base

    with sqlite3.connect(db) as conn:
        rows = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT service, failures FROM integration_health"
            )
        }
    assert rows["qbittorrent"] == 0
    assert rows["tmdb"] == 1

    attention.run_checks(db, config)
    item = next(
        item
        for item in attention.collect(db, config)
        if item.key == "integration:tmdb"
    )
    assert item.severity == "warn"


def test_job_failures_ignore_missing_config_and_tracebacks(tmp_path):
    db = _db(tmp_path)
    config = _config(
        tmp_path,
        qbittorrent_pass="x",
        prowlarr_api_key="x",
        jellyfin_api_key="x",
        tmdb_api_key="x",
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs(kind, state, error, finished_at) "
            "VALUES ('rss-sync', 'error', 'sin API key de Prowlarr\nTraceback...', "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )
        conn.commit()
    assert "jobs:error" not in _keys(attention.collect(db, config))

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs(kind, state, error, finished_at) "
            "VALUES ('metadata', 'error', 'HTTP 500 en TMDB\nTraceback...', "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )
        conn.commit()
    item = next(
        item for item in attention.collect(db, config) if item.key == "jobs:error"
    )
    assert item.detail.endswith("HTTP 500 en TMDB")


def test_counts_and_status_shape(tmp_path):
    data = attention.status(_db(tmp_path), _config(tmp_path))
    assert set(data) == {"items", "counts", "generated_at"}
    assert data["counts"]["total"] == len(data["items"])
    assert data["counts"]["fail"] >= 1
