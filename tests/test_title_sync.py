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
