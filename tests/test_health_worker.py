import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.health_worker import (
    best_match,
    build_query,
    clean_title,
    run,
    score_match,
    size_close,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
SIZE = 3_000_000_000
SIZE_CLOSE = 3_010_000_000
SIZE_WRONG = 4_000_000_000


def seed(tmp_path, kind="movie", season=None, episode=None):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            "series:tmdb:2:1:3" if kind == "series" else "movie:tmdb:1",
            kind,
            1,
            "Test Movie",
            2020,
            season,
            episode,
        ),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders,info_hash) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            asset_id,
            "TrackerEspanol2",
            "Test.Movie.1080p.mkv",
            SIZE,
            "unknown",
            0,
            None,
        ),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.commit()
    conn.close()
    return db, release_id


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, media_type="movie", limit=50, indexer_ids=None):
        self.calls.append(
            {"query": query, "media_type": media_type, "limit": limit}
        )
        return self.results


def test_clean_and_size_helpers():
    assert clean_title("Test.Movie.1080p.mkv") == "testmovie1080p"
    assert size_close(SIZE, SIZE_CLOSE)
    assert not size_close(SIZE, SIZE_WRONG)
    assert not size_close(None, SIZE)


def test_score_match_requires_title_and_size():
    release = {
        "title": "Test.Movie.1080p.mkv",
        "size_bytes": SIZE,
        "info_hash": None,
        "tracker": "TrackerEspanol2",
    }
    exact = {
        "title": "Test.Movie.1080p.mkv",
        "size": SIZE,
        "indexer": "TrackerEspanol2",
    }
    wrong_size = {
        "title": "Test.Movie.1080p.mkv",
        "size": SIZE_WRONG,
        "indexer": "TrackerEspanol2",
    }
    assert score_match(release, exact) >= 200
    assert score_match(release, wrong_size) < 100


def test_best_match_rejects_below_threshold():
    release = {
        "title": "Test.Movie.1080p.mkv",
        "size_bytes": SIZE,
        "info_hash": None,
        "tracker": "TrackerEspanol2",
    }
    results = [
        {"title": "Otra.Cosa.mkv", "size": SIZE, "seeders": 50},
        {"title": "Test.Movie.1080p.mkv", "size": SIZE_WRONG, "seeders": 50},
    ]
    assert best_match(release, results) is None


def test_build_query_for_series():
    asset = {
        "asset_title": "Test Show S01E03",
        "title": "test show s01e03",
        "asset_year": 2020,
        "kind": "series",
        "season": 1,
        "episode": 3,
    }
    assert build_query(asset) == "Test Show S01E03 2020"


def test_build_query_does_not_duplicate_year_or_episode():
    movie = {
        "asset_title": "Bad Boys 1995",
        "title": "bad boys",
        "asset_year": 1995,
        "kind": "movie",
        "season": None,
        "episode": None,
    }
    assert build_query(movie) == "Bad Boys 1995"
    series = {
        "asset_title": "Test Show",
        "title": "test show",
        "asset_year": 2020,
        "kind": "series",
        "season": 1,
        "episode": 3,
    }
    assert build_query(series) == "Test Show 2020 S01E03"


def test_run_updates_alive(tmp_path):
    db, release_id = seed(tmp_path)
    client = FakeClient(
        [
            {
                "title": "Test.Movie.1080p.mkv",
                "size": SIZE,
                "seeders": 50,
                "peers": 2,
                "indexer": "TrackerEspanol2",
                "guid": "guid-1",
                "publishDate": "2026-01-01T00:00:00Z",
            }
        ]
    )
    report = run(db, client, limit=1, delay=0, now=NOW)
    assert report["checked"] == 1
    assert report["alive"] == 1
    assert report["items"][0]["health"] == "alive"
    assert client.calls[0]["query"] == "Test Movie 2020"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    conn.close()
    assert row["health"] == "alive"
    assert row["seeders"] == 50
    assert row["peers"] == 2
    assert row["live_title"] == "Test.Movie.1080p.mkv"
    assert row["live_tracker"] == "TrackerEspanol2"
    assert row["live_guid"] == "guid-1"


def test_run_no_match_marks_dead(tmp_path):
    db, release_id = seed(tmp_path)
    client = FakeClient([])
    report = run(db, client, limit=1, delay=0, now=NOW)
    assert report["dead"] == 1
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT health FROM releases WHERE id=?", (release_id,)
    ).fetchone()
    conn.close()
    assert row[0] == "dead"


def test_dry_run_does_not_update(tmp_path):
    db, release_id = seed(tmp_path)
    client = FakeClient(
        [{"title": "Test.Movie.1080p.mkv", "size": SIZE, "seeders": 50}]
    )
    report = run(db, client, limit=1, delay=0, now=NOW, dry_run=True)
    assert report["alive"] == 1
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT health, seeders FROM releases WHERE id=?", (release_id,)
    ).fetchone()
    conn.close()
    assert row == ("unknown", 0)


def test_series_query_uses_media_type(tmp_path):
    db, _release_id = seed(tmp_path, kind="series", season=1, episode=3)
    client = FakeClient([])
    run(db, client, limit=1, delay=0, now=NOW)
    assert client.calls[0]["media_type"] == "series"
    assert "S01E03" in client.calls[0]["query"]
