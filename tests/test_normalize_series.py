import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.normalize_series import normalize_series_titles

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeTmdb:
    def __init__(self):
        self.calls = []

    def tv_details(self, tmdb_id):
        self.calls.append(tmdb_id)
        return {
            "id": tmdb_id,
            "name": "ONE PIECE",
            "original_name": "ONE PIECE",
            "first_air_date": "2023-08-31",
            "original_language": "en",
            "popularity": 24.0,
        }


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for episode in (1, 5):
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                f"series:tmdb:111110:1:{episode}",
                "series",
                111110,
                f"one piece s01e{episode:02d} release",
                2026,
                1,
                episode,
            ),
        )
    conn.commit()
    conn.close()
    return db


def test_normalize_series_updates_all_episodes(tmp_path):
    db = seed(tmp_path)
    client = FakeTmdb()
    report = normalize_series_titles(db, client, delay=0)
    assert report["series_checked"] == 1
    assert report["series_updated"] == 1
    assert report["episodes_updated"] == 2
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT DISTINCT title, year FROM media_items WHERE kind='series'"
    ).fetchall()
    assert rows == [("ONE PIECE", 2023)]
    conn.close()

    again = normalize_series_titles(db, client, delay=0)
    assert again["already_ok"] == 1
    assert again["episodes_updated"] == 0


def test_normalize_series_dry_run_does_not_write(tmp_path):
    db = seed(tmp_path)
    report = normalize_series_titles(
        db, FakeTmdb(), delay=0, dry_run=True
    )
    assert report["series_updated"] == 1
    assert report["episodes_updated"] == 0
    conn = sqlite3.connect(db)
    title = conn.execute(
        "SELECT title FROM media_items LIMIT 1"
    ).fetchone()[0]
    assert title.startswith("one piece s01e")
    conn.close()
