import sqlite3
from pathlib import Path

from catalogo import ondemand
from catalogo import admin
from catalogo.config import Config
from catalogo.db import init_db
from catalogo.metadata import backfill, pending_targets

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _config(tmp_path, db):
    return Config(
        db_path=db,
        legacy_db=db,
        library_root=tmp_path / "library",
        seed_root=tmp_path / "descargas",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:1/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1",
        qbittorrent_user="x",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:1/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=tmp_path / "catalogo",
    )


def _seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for season, episode in [(1, 1), (1, 2), (2, 1)]:
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, "
            "season, episode, popularity, poster_path) "
            "VALUES(?, ?, 'series', 42, 'Serie de prueba', 2020, ?, ?, ?, ?)",
            (
                f"series:tmdb:42:{season}:{episode}",
                f"series:tmdb:42:{season}:{episode}",
                season,
                episode,
                10 - episode,
                "/poster.jpg",
            ),
        )
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, popularity) "
        "VALUES('movie:tmdb:1', 'movie:tmdb:1', 'movie', 1, 'Los Valientes', 2020, 5)"
    )
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, popularity) "
        "VALUES('movie:tmdb:2', 'movie:tmdb:2', 'movie', 2, 'Alien 3', 1992, 3)"
    )
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, popularity) "
        "VALUES('movie:tmdb:3', 'movie:tmdb:3', 'movie', 3, 'Alien Covenant', 2017, 4)"
    )
    conn.commit()
    conn.close()
    return db


def test_catalog_groups_series_and_keeps_counts(tmp_path):
    db = _seed(tmp_path)
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="series", per_page=10, publication=""
    )
    assert data["group"] == "series"
    assert data["total"] == 4
    series = next(item for item in data["items"] if item["kind"] == "series")
    assert series["tmdb"] == 42
    assert series["episodes"] == 3
    assert series["seasons"] == [1, 2]
    assert series["download_counts"]["sin_descarga"] == 3
    assert data["facets"]["kind"] == {"movie": 3, "series": 1}


def test_specials_do_not_mark_series_incomplete(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, "
            "season, episode, popularity, library_added) "
            "VALUES('series:tmdb:43:0:1', 'series:tmdb:43:0:1', 'series', 43, "
            "'Serie con especiales', 2021, 0, 1, 1, 1)"
        )
        conn.commit()
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="series", per_page=20, incomplete=True
    )
    assert 43 not in [item.get("tmdb") for item in data["items"]]


def test_incomplete_library_filter(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE media_items SET library_added=1 "
            "WHERE key IN ('movie:tmdb:1', 'movie:tmdb:2')"
        )
        conn.execute(
            "INSERT INTO releases(asset_id, tracker, title, health) "
            "SELECT id, 'test', 'Alien 3', 'alive' FROM media_items "
            "WHERE key='movie:tmdb:2'"
        )
        conn.commit()
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="movie", per_page=10, incomplete=True
    )
    titles = [item["title"] for item in data["items"]]
    assert "Los Valientes" in titles
    assert "Alien 3" not in titles
    assert data["facets"]["incomplete"] == 1


def test_published_title_is_not_in_arr_by_default(tmp_path):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    original = ondemand.strm_inventory.is_key_published
    ondemand.strm_inventory.is_key_published = lambda item, keys: True
    try:
        data = ondemand.catalog(db, config, group="movie", per_page=10)
    finally:
        ondemand.strm_inventory.is_key_published = original
    item = next(
        entry for entry in data["items"] if entry["title"] == "Los Valientes"
    )
    assert item["published"] is True
    assert item["library_added"] is False
    assert item["publication"] == "publicado"


def test_arr_membership_is_not_publication(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE media_items SET library_added=1, library_source='radarr' "
            "WHERE key='movie:tmdb:1'"
        )
        conn.commit()
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="movie", per_page=10
    )
    item = next(
        entry for entry in data["items"] if entry["title"] == "Los Valientes"
    )
    assert item["library_added"] is True
    assert item["published"] is False
    assert item["publication"] == "sin_fuente"


def test_downloaded_without_release_is_pending(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        asset_id = conn.execute(
            "SELECT id FROM media_items WHERE key='movie:tmdb:1'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO torrent_files(asset_id, rel_path, storage_location, state) "
            "VALUES(?, 'Los Valientes.mkv', 'LOCAL_DISK', 'ready')",
            (asset_id,),
        )
        conn.commit()
    config = _config(tmp_path, db)
    pending = ondemand.catalog(
        db, config, group="movie", per_page=10, publication="pendiente"
    )
    assert "Los Valientes" in [item["title"] for item in pending["items"]]

    no_source = ondemand.catalog(
        db, config, group="movie", per_page=10, publication="sin_fuente"
    )
    assert "Los Valientes" not in [item["title"] for item in no_source["items"]]


def test_catalog_handles_series_without_tmdb(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, title, season, episode) "
            "VALUES('series:legacy:1:1', 'series:legacy:1:1', 'series', "
            "'Serie antigua', 1, 1)"
        )
        conn.commit()
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="series", per_page=20, publication=""
    )
    assert data["total"] >= 1


def test_catalog_episode_mode_keeps_episodes(tmp_path):
    db = _seed(tmp_path)
    data = ondemand.catalog(
        db, _config(tmp_path, db), group="episode", per_page=10, publication=""
    )
    assert data["total"] == 6
    assert sum(1 for item in data["items"] if item["kind"] == "series") == 3


def test_series_detail_returns_seasons_and_keys(tmp_path):
    db = _seed(tmp_path)
    detail = ondemand.series_detail(db, _config(tmp_path, db), 42)
    assert detail is not None
    assert detail["totals"]["episodes"] == 3
    assert [season["season"] for season in detail["seasons"]] == [1, 2]
    assert detail["seasons"][0]["episodes"][0]["key"] == "series:tmdb:42:1:1"
    assert detail["series"]["poster_path"] == "/poster.jpg"


def test_series_detail_uses_stored_episode_title(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE media_items SET episode_title='Las normas de la tienda' "
            "WHERE key='series:tmdb:42:1:1'"
        )
        conn.commit()
    detail = ondemand.series_detail(db, _config(tmp_path, db), 42)
    assert detail is not None
    episode = detail["seasons"][0]["episodes"][0]
    assert episode["name"] == "Las normas de la tienda"


def test_search_prefers_prefix_over_substring(tmp_path):
    db = _seed(tmp_path)
    data = ondemand.search_catalog(db, "alien", limit=2)
    titles = [item["title"] for item in data["items"]]
    assert titles and titles[0].startswith("Alien")
    assert "Los Valientes" not in titles


def test_pending_summary_distinguishes_pending_and_no_source(tmp_path):
    db = _seed(tmp_path)
    with sqlite3.connect(db) as conn:
        movie_id = conn.execute(
            "SELECT id FROM media_items WHERE key='movie:tmdb:2'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO releases(asset_id, tracker, title, size_bytes, seeders, "
            "health, source, privacy) VALUES(?, 'Tracker', 'Alien 3 1080p', 1000, 5, "
            "'alive', 'prowlarr', 'private')",
            (movie_id,),
        )
        series_id = conn.execute(
            "SELECT id FROM media_items WHERE key='series:tmdb:42:1:1'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO releases(asset_id, tracker, title, size_bytes, seeders, "
            "health, source, privacy) VALUES(?, 'Tracker', 'Serie T01E01', 1000, "
            "5, 'alive', 'prowlarr', 'private')",
            (series_id,),
        )
        conn.commit()
    config = _config(tmp_path, db)
    pending = ondemand.pending_summary(db, config, publication="pendiente")
    assert pending["total"] == 2
    assert pending["movies"] == 1
    assert pending["series_episodes"] == 1
    assert pending["series"] == 1
    no_source = ondemand.pending_summary(db, config, publication="sin_fuente")
    assert no_source["total"] == 4
    assert no_source["movies"] == 2
    assert no_source["series_episodes"] == 2
    targets = ondemand.pending_targets(
        db, config, publication="pendiente", kind="movie"
    )
    assert [target["title"] for target in targets] == ["Alien 3"]
    assert ondemand.pending_targets(
        db, config, publication="pendiente", q="valientes"
    ) == []


def test_series_detail_merges_tmdb_episodes(tmp_path):
    db = _seed(tmp_path)
    tmdb_data = {
        "series": {
            "tmdb": 42,
            "title": "Serie TMDB",
            "year": 2020,
            "poster_path": "/tmdb.jpg",
            "overview": "Sinopsis TMDB",
            "vote_average": 8.2,
        },
        "seasons": [
            {
                "season": 1,
                "name": "Temporada 1",
                "episodes": [
                    {"episode": 1, "name": "Piloto", "air_date": "2020-01-01",
                     "overview": "Sinopsis piloto", "still_path": "/s1e1.jpg"},
                    {"episode": 2, "name": "Segundo", "air_date": "2020-01-08",
                     "still_path": "/s1e2.jpg"},
                    {"episode": 3, "name": "Tercero", "air_date": "2020-01-15",
                     "still_path": "/s1e3.jpg"},
                ],
            },
            {
                "season": 2,
                "name": "Temporada 2",
                "episodes": [
                    {"episode": 1, "name": "Vuelta", "air_date": "2021-01-01",
                     "still_path": "/s2e1.jpg"},
                ],
            },
        ],
        "errors": [],
    }
    detail = ondemand.series_detail(
        db, _config(tmp_path, db), 42, tmdb_data=tmdb_data
    )
    assert detail is not None
    assert detail["tmdb_ok"] is True
    assert detail["series"]["title"] == "Serie TMDB"
    assert detail["totals"] == {
        "episodes": 4,
        "catalog_episodes": 3,
        "missing_episodes": 1,
        "published": 0,
        "library": 0,
        "monitored": 0,
        "covered": 0,
        "downloaded": 0,
        "downloading": 0,
        "incomplete": 0,
        "seasons": 2,
    }
    episode = detail["seasons"][0]["episodes"][0]
    assert episode["name"] == "Piloto"
    assert episode["still_path"] == "/s1e1.jpg"
    assert episode["in_catalog"] is True
    missing = detail["seasons"][0]["episodes"][2]
    assert missing["id"] is None
    assert missing["in_catalog"] is False
    assert missing["publication"] == "sin_catalogo"

    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT episode_title, episode_overview FROM media_items "
            "WHERE key='series:tmdb:42:1:1'"
        ).fetchone()
    assert stored == ("Piloto", "Sinopsis piloto")


def test_manual_request_does_not_fake_arr_membership(tmp_path):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    result = admin.set_library(
        config, series_tmdb=42, seasons=[1], monitored=True
    )
    assert result["titles"] == 2
    detail = ondemand.series_detail(db, config, 42)
    assert detail["totals"]["library"] == 0
    assert detail["totals"]["monitored"] == 2
    assert detail["totals"]["covered"] == 2
    assert detail["totals"]["published"] == 0
    season = detail["seasons"][0]
    assert season["library"] == 0
    assert season["monitored"] == 2
    assert not any(episode["library_added"] for episode in season["episodes"])
    assert all(episode["monitored"] for episode in season["episodes"])
    other = detail["seasons"][1]
    assert other["library"] == 0
    assert not any(episode["library_added"] for episode in other["episodes"])


def test_monitor_only_checks_available_titles(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, "
            "release_date, monitored) VALUES('movie:tmdb:9', "
            "'movie:tmdb:9', 'movie', 9, 'Disponible', 2000, "
            "'2000-01-01', 1)"
        )
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, "
            "release_date, monitored) VALUES('movie:tmdb:10', "
            "'movie:tmdb:10', 'movie', 10, 'Futura', 2999, "
            "'2999-01-01', 1)"
        )
        conn.execute(
            "UPDATE media_items SET monitored=1, air_date='2000-01-01' "
            "WHERE key='series:tmdb:42:1:1'"
        )
        conn.execute(
            "UPDATE media_items SET monitored=1, air_date='2999-01-01' "
            "WHERE key='series:tmdb:42:1:2'"
        )
        conn.commit()
    checked = []
    monkeypatch.setattr(
        admin, "_sync_keys", lambda ctx, keys, **kwargs: checked.extend(keys)
    )
    monkeypatch.setattr(admin, "export_strm", lambda *args, **kwargs: None)
    report = admin.monitor_titles(db, config, delay=0)
    assert set(checked) == {"movie:tmdb:9", "series:tmdb:42:1:1"}
    assert report["waiting"] == 2


def test_monitored_items_builds_wanted_queue(tmp_path):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE media_items SET monitored=1, release_date='2000-01-01' "
            "WHERE key='movie:tmdb:1'"
        )
        conn.execute(
            "UPDATE media_items SET monitored=1, release_date='2999-01-01' "
            "WHERE key='movie:tmdb:2'"
        )
        conn.execute(
            "UPDATE media_items SET monitored=1, air_date='2000-01-01' "
            "WHERE key='series:tmdb:42:1:1'"
        )
        conn.commit()
    data = ondemand.monitored_items(db, config)
    assert data["summary"] == {
        "total": 3,
        "missing": 2,
        "pending": 1,
        "downloading": 0,
    }
    states = {item["key"]: item["state"] for item in data["items"]}
    assert states["movie:tmdb:1"] == "waiting"
    assert states["movie:tmdb:2"] == "pending"
    assert states["series:tmdb:42:1:1"] == "waiting"
    only_missing = ondemand.monitored_items(db, config, state="missing")
    assert only_missing["total"] == 2
    only_movies = ondemand.monitored_items(db, config, kind="movie")
    assert only_movies["total"] == 2


def test_metadata_backfill_updates_all_episodes(tmp_path):
    db = _seed(tmp_path)

    class FakeTmdb:
        def movie_details(self, tmdb):
            return {
                "poster_path": f"/m{tmdb}.jpg",
                "backdrop_path": None,
                "overview": "Sinopsis",
                "vote_average": 7.5,
            }

        def tv_details(self, tmdb):
            return {
                "poster_path": "/serie.jpg",
                "backdrop_path": "/fondo.jpg",
                "overview": "Sinopsis de serie",
                "vote_average": 8.0,
            }

    assert len(pending_targets(db)) == 4
    report = backfill(db, FakeTmdb(), workers=2)
    assert report["updated"] == 4
    assert report["failed"] == 0
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT poster_path, overview, vote_average, metadata_at "
            "FROM media_items WHERE kind='series' AND tmdb=42"
        ).fetchall()
    assert len(rows) == 3
    assert all(row[0] == "/serie.jpg" for row in rows)
    assert all(row[1] == "Sinopsis de serie" for row in rows)
    assert all(row[2] == 8.0 for row in rows)
    assert all(row[3] for row in rows)
    assert pending_targets(db) == []


def test_recent_published_only_returns_titles_with_strm(tmp_path):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute(
        "SELECT key, kind, title, year, season, episode FROM media_items "
        "WHERE key='movie:tmdb:1'"
    ).fetchone())
    conn.close()
    from catalogo.strm import strm_path

    path = strm_path(config.catalog_library, row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("http://127.0.0.1:8773/play?key=movie:tmdb:1", encoding="utf-8")
    data = ondemand.recent_published(db, config)
    assert [item["key"] for item in data["items"]] == ["movie:tmdb:1"]


def test_catalog_cache_invalidates_on_user_action(tmp_path):
    db = _seed(tmp_path)
    config = _config(tmp_path, db)
    ondemand.invalidate_catalog()
    first = ondemand.catalog(db, config, per_page=10)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year, popularity) "
        "VALUES('movie:tmdb:99', 'movie:tmdb:99', 'movie', 99, 'Nueva', 2026, 1)"
    )
    conn.commit()
    conn.close()
    cached = ondemand.catalog(db, config, per_page=10)
    assert cached["total"] == first["total"]
    ondemand.invalidate_catalog()
    fresh = ondemand.catalog(db, config, per_page=10)
    assert fresh["total"] == first["total"] + 1
