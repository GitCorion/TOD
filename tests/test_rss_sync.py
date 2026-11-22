import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.rss_sync import publish_keys, sync_rss

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeProwlarr:
    def __init__(self, movies=None, series=None):
        self.movies = movies or []
        self.series = series or []

    def indexers(self):
        return [
            {"id": 1, "name": "TrackerEspanol (API)", "language": "es-ES"},
            {"id": 5, "name": "TrackerEspanol2 (API)", "language": "es-ES"},
        ]

    def search(self, query, *, media_type="movie", limit=50, indexer_ids=None):
        return list(self.movies if media_type == "movie" else self.series)


class FakeJackett:
    def search(self, slug, query, *, media_type="movie", limit=50):
        return []


class FakeTmdb:
    def movie_details(self, tmdb_id):
        return {
            "id": tmdb_id,
            "title": "Test Movie",
            "original_title": "Test Movie",
            "release_date": "2020-01-01",
            "popularity": 1.0,
        }

    def tv_details(self, tmdb_id):
        return {
            "id": tmdb_id,
            "name": "Test Show",
            "original_name": "Test Show",
            "first_air_date": "2021-01-01",
            "popularity": 1.0,
        }

    def search_movie(self, query, *, year=None, page=1):
        return {"results": []}

    def search_tv(self, query, *, year=None, page=1):
        return {"results": []}


def movie_result(**overrides):
    base = {
        "title": "Test.Movie.2020.1080p.WEB-DL.mkv",
        "size": 1000,
        "seeders": 10,
        "peers": 1,
        "indexer": "TrackerEspanol (API)",
        "guid": "m1",
        "infoHash": "hash-movie",
        "downloadUrl": "http://prowlarr/1",
        "tmdbId": 1,
        "publishDate": "2026-11-19T10:00:00Z",
    }
    base.update(overrides)
    return base


def episode_result(**overrides):
    base = {
        "title": "Test.Show.S01E03.1080p.WEB-DL.mkv",
        "size": 2000,
        "seeders": 5,
        "peers": 1,
        "indexer": "TrackerEspanol2 (API)",
        "guid": "s1",
        "infoHash": "hash-series",
        "downloadUrl": "http://prowlarr/2",
        "tmdbId": 2,
        "publishDate": "2026-11-19T09:00:00Z",
    }
    base.update(overrides)
    return base


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def test_rss_creates_movie_and_episode(tmp_path):
    db = seed(tmp_path)
    client = FakeProwlarr([movie_result()], [episode_result()])
    report = sync_rss(
        db, client, FakeJackett(), FakeTmdb(), limit=10, delay=0
    )
    assert report["movies"] == 1
    assert report["series"] == 1
    assert report["inserted"] == 2
    assert report["new_titles"] == 2
    assert set(report["keys"]) == {
        "movie:tmdb:1", "series:tmdb:2:1:3"
    }

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key, kind, title, year FROM media_items ORDER BY key"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("movie:tmdb:1", "movie", "Test Movie", 2020),
        ("series:tmdb:2:1:3", "series", "Test Show", 2021),
    ]
    releases = conn.execute(
        "SELECT tracker, audio, audio_confidence, seeders FROM releases "
        "ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in releases] == [
        ("TrackerEspanol (API)", "castellano", "tracker", 10),
        ("TrackerEspanol2 (API)", "castellano", "tracker", 5),
    ]
    conn.close()


def test_publish_keys_closes_monitoring_when_strm_exists(tmp_path, monkeypatch):
    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, monitored) "
        "VALUES('movie:tmdb:1', 'movie:tmdb:1', 'movie', 1, 'Test', 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("catalogo.strm.export_strm", lambda *args, **kwargs: None)
    monkeypatch.setattr("catalogo.strm_inventory.invalidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "catalogo.strm_inventory.inventory",
        lambda *args, **kwargs: {"keys": {"movie:tmdb:1"}},
    )
    report = publish_keys(
        db, tmp_path / "catalogo", ["movie:tmdb:1"], base_url="http://127.0.0.1"
    )
    assert report == {"exported": 1, "unmonitored": 1}
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT monitored FROM media_items WHERE key='movie:tmdb:1'"
    ).fetchone()[0] == 0
    conn.close()


def test_rss_second_run_updates_without_duplicates(tmp_path):
    db = seed(tmp_path)
    client = FakeProwlarr([movie_result()], [episode_result()])
    tmdb = FakeTmdb()
    sync_rss(db, client, FakeJackett(), tmdb, limit=10, delay=0)
    report = sync_rss(
        db, client, FakeJackett(), tmdb, limit=10, delay=0
    )
    assert report["inserted"] == 0
    assert report["updated"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 2
    conn.close()


def test_rss_skips_packs_and_dry_run(tmp_path):
    db = seed(tmp_path)
    pack = episode_result(
        title="Test.Show.S01.1080p.PACK.mkv", guid="pack"
    )
    client = FakeProwlarr([movie_result()], [pack])
    report = sync_rss(
        db, client, FakeJackett(), FakeTmdb(),
        limit=10, delay=0, dry_run=True,
    )
    assert report["inserted"] == 1
    assert report["skipped"][0]["reason"] == "pack"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
    conn.close()
