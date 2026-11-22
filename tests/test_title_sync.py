import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.title_sync import upsert_movies

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def test_upsert_movies_inserts_and_updates(tmp_path):
    db = seed_db(tmp_path)
    items = [
        {
            "tmdb": 1,
            "title": "En el corazón de la bestia",
            "original_title": "Heart of the Beast",
            "year": 2026,
            "original_language": "en",
            "popularity": 10.5,
        }
    ]
    first = upsert_movies(db, items)
    assert first["inserted"] == 1
    assert first["updated"] == 0

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT key, kind, title, original_title, year, popularity, "
        "external_source FROM media_items"
    ).fetchone()
    assert row == (
        "movie:tmdb:1",
        "movie",
        "En el corazón de la bestia",
        "Heart of the Beast",
        2026,
        10.5,
        "tmdb",
    )

    items[0]["title"] = "Otro título"
    items[0]["popularity"] = 99.0
    second = upsert_movies(db, items)
    assert second["inserted"] == 0
    assert second["updated"] == 1
    row = conn.execute(
        "SELECT title, original_title, popularity FROM media_items"
    ).fetchone()
    assert row == ("Otro título", "Heart of the Beast", 99.0)
    conn.close()


def test_upsert_movies_skips_invalid_and_stores_aliases(tmp_path):
    db = seed_db(tmp_path)
    report = upsert_movies(
        db,
        [
            {"tmdb": 0, "title": "Sin id"},
            {"tmdb": 2, "title": ""},
            {
                "tmdb": 3,
                "title": "La Odisea",
                "original_title": "The Odyssey",
                "year": 2026,
                "aliases": ["Odyssey", "La Odisea (2026)"],
            },
        ],
    )
    assert report == {
        "inserted": 1,
        "updated": 0,
        "skipped": 2,
        "source": "tmdb",
    }
    conn = sqlite3.connect(db)
    aliases = conn.execute(
        "SELECT aliases FROM media_items WHERE key='movie:tmdb:3'"
    ).fetchone()[0]
    assert aliases == '["Odyssey", "La Odisea (2026)"]'
    conn.close()


def test_upsert_movies_keeps_first_origin(tmp_path):
    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 9, "title": "Con procedencia", "year": 2024}],
        origin="import",
    )
    upsert_movies(
        db,
        [{"tmdb": 9, "title": "Con procedencia", "year": 2024}],
        source="rss",
        origin="rss",
    )
    conn = sqlite3.connect(db)
    origin, detail = conn.execute(
        "SELECT origin, origin_detail FROM media_items WHERE key='movie:tmdb:9'"
    ).fetchone()
    conn.close()
    assert origin == "import"
    assert detail is None


def test_upsert_episodes_inserts_and_updates(tmp_path):
    from catalogo.title_sync import upsert_episodes

    db = seed_db(tmp_path)
    series = {
        "tmdb": 111110,
        "title": "ONE PIECE",
        "original_title": "ONE PIECE",
        "year": 2023,
        "popularity": 24.0,
    }
    episodes = [
        {"season": 1, "episode": 1, "name": "Piloto", "overview": "Primero"},
        {"season": 1, "episode": 5, "name": "Quinto", "overview": "Quinto capítulo"},
    ]
    first = upsert_episodes(db, series, episodes)
    assert first["inserted"] == 2
    assert first["updated"] == 0

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT key, kind, season, episode, title, episode_title, episode_overview "
        "FROM media_items WHERE kind='series' ORDER BY episode"
    ).fetchall()
    assert rows == [
        ("series:tmdb:111110:1:1", "series", 1, 1, "ONE PIECE", "Piloto", "Primero"),
        ("series:tmdb:111110:1:5", "series", 1, 5, "ONE PIECE", "Quinto", "Quinto capítulo"),
    ]

    series["title"] = "One Piece (Netflix)"
    episodes[0]["name"] = "Piloto (remaster)"
    episodes[0]["overview"] = "Primero, remasterizado"
    second = upsert_episodes(db, series, episodes)
    assert second["inserted"] == 0
    assert second["updated"] == 2
    titles = conn.execute(
        "SELECT DISTINCT title FROM media_items WHERE kind='series'"
    ).fetchall()
    assert titles == [("One Piece (Netflix)",)]
    names = conn.execute(
        "SELECT episode_title, episode_overview FROM media_items "
        "WHERE kind='series' ORDER BY episode"
    ).fetchall()
    assert names == [
        ("Piloto (remaster)", "Primero, remasterizado"),
        ("Quinto", "Quinto capítulo"),
    ]
    conn.close()


def test_new_catalog_titles_are_monitored_by_default(tmp_path):
    from catalogo.title_sync import upsert_episodes

    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 11, "title": "Película nueva", "year": 2026}],
        source="tmdb",
    )
    upsert_episodes(
        db,
        {"tmdb": 12, "title": "Serie nueva", "year": 2026},
        [{"season": 1, "episode": 1, "name": "Piloto"}],
        source="rss",
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    movie = conn.execute(
        "SELECT monitored, monitor_source FROM media_items "
        "WHERE key='movie:tmdb:11'"
    ).fetchone()
    episode = conn.execute(
        "SELECT monitored, monitor_source FROM media_items "
        "WHERE key='series:tmdb:12:1:1'"
    ).fetchone()
    assert (movie["monitored"], movie["monitor_source"]) == (1, "tmdb")
    assert (episode["monitored"], episode["monitor_source"]) == (1, "rss")
    conn.close()


def test_arr_imports_are_not_monitored_by_default(tmp_path):
    from catalogo.title_sync import upsert_episodes

    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 21, "title": "En Radarr", "year": 2026}],
        source="radarr",
    )
    upsert_episodes(
        db,
        {"tmdb": 22, "title": "En Sonarr", "year": 2026},
        [{"season": 1, "episode": 1, "name": "Piloto"}],
        source="sonarr",
    )
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT monitored, monitor_source FROM media_items ORDER BY key"
    ).fetchall()
    assert rows == [(0, None), (0, None)]
    conn.close()


def test_update_does_not_re_monitor_an_existing_title(tmp_path):
    db = seed_db(tmp_path)
    upsert_movies(db, [{"tmdb": 31, "title": "Ya publicada"}])
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE media_items SET monitored=0, monitor_source=NULL "
        "WHERE key='movie:tmdb:31'"
    )
    conn.commit()
    conn.close()
    upsert_movies(db, [{"tmdb": 31, "title": "Ya publicada (actualizada)"}])
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT monitored FROM media_items WHERE key='movie:tmdb:31'"
    ).fetchone()[0] == 0
    conn.close()
