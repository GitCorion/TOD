import json
import urllib.request
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from catalogo import integrations, trailarr
from catalogo.config import Config
from catalogo.db import connect, init_db
from catalogo.naming import save_naming_settings
from catalogo.web import make_server

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _config(tmp_path: Path, **overrides) -> Config:
    catalog_dir = tmp_path / "catalogo"
    library_dir = tmp_path / "library"
    downloads_dir = tmp_path / "descargas"
    ondemand_dir = downloads_dir / "ondemand"
    for d in (catalog_dir, library_dir, downloads_dir, ondemand_dir):
        d.mkdir(parents=True, exist_ok=True)
    values = dict(
        db_path=tmp_path / "tod.db",
        legacy_db=tmp_path / "assets.db",
        library_root=library_dir,
        seed_root=downloads_dir,
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:9696/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:8080",
        qbittorrent_user="admin",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:9117/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=catalog_dir,
        downloads_root=downloads_dir,
        ondemand_save_path=ondemand_dir,
        trailarr_url="http://127.0.0.1:7889",
        trailarr_api_key="trailarr-secret",
    )
    values.update(overrides)
    return Config(**values)


def _seed_media(db_path: Path, catalog_library: Path | None = None) -> None:
    init_db(db_path, MIGRATIONS)
    if catalog_library is not None:
        (catalog_library / "movies" / "Interestelar (2014)").mkdir(
            parents=True, exist_ok=True
        )
        (catalog_library / "series" / "Juego de Tronos (2011)").mkdir(
            parents=True, exist_ok=True
        )
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO media_items(
                key, kind, title, original_title, year, tmdb, imdb_id,
                overview, poster_path, library_added
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "movie:tmdb:157336",
                "movie",
                "Interestelar",
                "Interstellar",
                2014,
                157336,
                "tt0816692",
                "Viaje espacial.",
                "/poster.jpg",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO media_items(
                key, kind, title, original_title, year, tmdb, library_added
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("movie:tmdb:888888", "movie", "Sin Carpeta En Disco", "No Dir", 2021, 888888, 1),
        )
        conn.execute(
            """
            INSERT INTO media_items(
                key, kind, title, original_title, year, tmdb, library_added
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("movie:tmdb:999999", "movie", "No En Catalogo", "Not Added", 2025, 999999, 0),
        )
        conn.execute(
            """
            INSERT INTO media_items(
                key, kind, title, original_title, year, tmdb, tvdb_id,
                season, episode, series_type, is_anime, library_added
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "series:tmdb:1399:1:1",
                "series",
                "Juego de Tronos",
                "Game of Thrones",
                2011,
                1399,
                121361,
                1,
                1,
                "standard",
                0,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO media_items(
                key, kind, title, original_title, year, tmdb, tvdb_id,
                season, episode, series_type, is_anime, library_added
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "series:tmdb:1399:1:2",
                "series",
                "Juego de Tronos",
                "Game of Thrones",
                2011,
                1399,
                121361,
                1,
                2,
                "standard",
                0,
                1,
            ),
        )
        conn.commit()


def test_radarr_and_sonarr_bridge_paths(tmp_path):
    cfg = _config(tmp_path)
    _seed_media(cfg.db_path, cfg.catalog_library)

    movies = trailarr.radarr_movies(cfg.db_path, cfg)
    assert len(movies) == 1
    assert movies[0]["tmdbId"] == 157336
    assert movies[0]["path"] == str(cfg.catalog_library / "movies" / "Interestelar (2014)")
    assert movies[0]["hasFile"] is True

    series = trailarr.sonarr_series(cfg.db_path, cfg)
    assert len(series) == 1
    assert series[0]["tmdbId"] == 1399
    assert series[0]["tvdbId"] == 121361
    assert series[0]["path"] == str(cfg.catalog_library / "series" / "Juego de Tronos (2011)")

    r_roots = trailarr.radarr_root_folders(cfg.db_path, cfg)
    s_roots = trailarr.sonarr_root_folders(cfg.db_path, cfg)
    assert r_roots[0]["path"] == str(cfg.catalog_library / "movies")
    assert s_roots[0]["path"] == str(cfg.catalog_library / "series")


def test_sonarr_bridge_without_season_folders(tmp_path):
    cfg = _config(tmp_path)
    _seed_media(cfg.db_path, cfg.catalog_library)
    save_naming_settings(cfg.db_path, {"use_season_folders": False})

    series = trailarr.sonarr_series(cfg.db_path, cfg)
    assert len(series) == 1
    assert series[0]["path"] == str(cfg.catalog_library / "series" / "Juego de Tronos (2011)")


def test_trailarr_client_and_sync(monkeypatch, tmp_path):
    cfg = _config(tmp_path)
    calls = []

    class _FakeResp:
        def __init__(self, payload):
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=20.0):
        calls.append((req.full_url, req.get_header("X-api-key")))
        if req.full_url.endswith("/api/v1/settings/"):
            return _FakeResp({"app_version": "0.5.0"})
        if req.full_url.endswith("/api/v1/connections/"):
            return _FakeResp([{"id": 1, "name": "TOD-Radarr"}])
        if req.full_url.endswith("/api/v1/tasks/"):
            return _FakeResp([{"id": 7, "name": "Refresh Media"}])
        if req.full_url.endswith("/api/v1/tasks/run/7"):
            return _FakeResp({"ok": True})
        raise AssertionError(f"unexpected url: {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = integrations.test_integration("trailarr", cfg)
    assert res["ok"] is True
    assert "0.5.0" in res["detail"]

    sync_res = trailarr.sync_trailarr(cfg)
    assert sync_res["ok"] is True
    assert sync_res["connections"] == 1
    assert sync_res["triggered"] == ["Refresh Media"]
    assert all(key == "trailarr-secret" for _, key in calls)


def test_trailarr_integration_missing_key(tmp_path):
    cfg = _config(tmp_path, trailarr_api_key=None)
    res = integrations.test_integration("trailarr", cfg)
    assert res["ok"] is False
    assert res["kind"] == "missing"


def test_trailarr_http_bridge_auth_and_endpoints(tmp_path):
    cfg = _config(tmp_path, web_token="tod-secret")
    _seed_media(cfg.db_path, cfg.catalog_library)
    app = None
    server = make_server("127.0.0.1", 0, cfg)
    port = server.server_address[1]
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}/trailarr/radarr/api/v3/movie", timeout=5)
        assert exc_info.value.code == 401

        req = urllib.request.Request(
            f"{base}/trailarr/radarr/api/v3/movie",
            headers={"X-Api-Key": "tod-secret"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            movies = json.loads(resp.read())
        assert len(movies) == 1
        assert movies[0]["title"] == "Interestelar"

        with urllib.request.urlopen(
            f"{base}/trailarr/sonarr/api/v3/series?apikey=tod-secret", timeout=5
        ) as resp:
            series = json.loads(resp.read())
        assert len(series) == 1
        assert series[0]["title"] == "Juego de Tronos"
    finally:
        server.shutdown()
        server.server_close()
