import sqlite3
from pathlib import Path

from catalogo.bulk_sync import (
    build_query,
    matches_asset,
    sync_sources,
)
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeProwlarr:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def indexers(self):
        return [
            {"id": 1, "name": "TrackerEspanol (API)", "language": "es-ES"},
            {"id": 5, "name": "TrackerEspanol2 (API)", "language": "es-ES"},
            {"id": 7, "name": "TrackerDeportes (API)", "language": "es-MX",
             "enable": False},
            {"id": 20, "name": "TrackerInternacional (API)", "language": "en-US"},
        ]

    def search(self, query, *, media_type="movie", limit=50, indexer_ids=None):
        self.searches.append((query, tuple(indexer_ids or ())))
        return self.results


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,original_title,year,"
        "popularity) VALUES(?,?,?,?,?,?,?)",
        (
            "movie:tmdb:1263337",
            "movie",
            1263337,
            "En el corazón de la bestia",
            "Heart of the Beast",
            2026,
            50.0,
        ),
    )
    conn.commit()
    conn.close()
    return db


def asset(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM media_items").fetchone()
    conn.close()
    return row


def result(**overrides):
    base = {
        "title": "Heart.of.the.Beast.2026.1080p.WEB-DL.mkv",
        "size": 8 * 1024**3,
        "seeders": 12,
        "peers": 1,
        "indexer": "TrackerEspanol (API)",
        "guid": "abc",
        "infoHash": "abc123",
        "downloadUrl": "http://prowlarr/1",
        "infoUrl": "https://tracker/torrent/1",
    }
    base.update(overrides)
    return base


def test_build_query_prefers_original_title():
    class Row(dict):
        pass

    row = Row(
        key="movie:tmdb:1",
        title="En el corazón de la bestia",
        original_title="Heart of the Beast",
        year=2026,
    )
    assert build_query(row) == "Heart of the Beast 2026"


def test_matches_by_tmdb_even_with_different_title(tmp_path):
    db = seed_db(tmp_path)
    row = asset(db)
    assert matches_asset(row, result(title="Titulo.Que.No.Se.Parece.2026", tmdbId=1263337))
    assert not matches_asset(row, result(title="Otra.Pelicula.2026", tmdbId=999))


def test_matches_by_original_title_and_year(tmp_path):
    db = seed_db(tmp_path)
    row = asset(db)
    assert matches_asset(row, result(title="Heart.of.the.Beast.2026.1080p.mkv"))
    assert not matches_asset(row, result(title="Heart.of.the.Beast.2019.1080p.mkv"))


def test_sync_inserts_release_with_tracker_audio(tmp_path):
    db = seed_db(tmp_path)
    client = FakeProwlarr([result()])
    report = sync_sources(db, client, limit=5, delay=0)
    assert report["assets_checked"] == 1
    assert report["inserted"] == 1
    assert "TrackerEspanol (API)" in [name for name, _ in report["indexers"]]
    # TrackerDeportes no participa
    assert all(7 not in ids for _, ids in client.searches)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    release = conn.execute("SELECT * FROM releases").fetchone()
    assert release["tracker"] == "TrackerEspanol (API)"
    assert release["info_url"] == "https://tracker/torrent/1"
    assert release["audio"] == "castellano"
    assert release["audio_confidence"] == "tracker"
    assert release["health"] == "alive"
    assert release["seeders"] == 12
    conn.close()


class LockProbeProwlarr(FakeProwlarr):
    """La búsqueda debe correr sin una transacción de escritura abierta."""

    db = None

    def search(self, query, **kwargs):
        conn = sqlite3.connect(self.db, timeout=0.2)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        finally:
            conn.close()
        return super().search(query, **kwargs)


def test_sync_does_not_hold_write_lock_during_search(tmp_path):
    db = seed_db(tmp_path)
    client = LockProbeProwlarr([result()])
    client.db = db
    report = sync_sources(db, client, limit=5, delay=0)
    assert report["errors"] == []
    assert report["inserted"] == 1


def test_international_tracker_without_tag_stays_unknown(tmp_path):
    db = seed_db(tmp_path)
    client = FakeProwlarr([result(indexer="TrackerInternacional (API)", guid="sd1")])
    sync_sources(db, client, limit=5, delay=0)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT audio, audio_confidence FROM releases"
    ).fetchone()
    assert row == ("unknown", None)
    conn.close()


def test_dry_run_does_not_write(tmp_path):
    db = seed_db(tmp_path)
    client = FakeProwlarr([result()])
    report = sync_sources(db, client, limit=5, delay=0, dry_run=True)
    assert report["accepted"] == 1
    assert report["inserted"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
    conn.close()


class FakeJackett:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def search(self, indexer, query, *, media_type="movie", limit=50):
        self.searches.append((indexer, query))
        return self.results


def test_jackett_results_are_merged_and_sanitized(tmp_path):
    db = seed_db(tmp_path)
    client = FakeProwlarr([])
    jackett = FakeJackett(
        [
            {
                "title": "Heart.of.the.Beast.2026.1080p.mkv Spanish",
                "size": 1000,
                "seeders": 5,
                "peers": 0,
                "infohash": "",
                "link": (
                    "http://127.0.0.1:9117/dl/trackerjackett/"
                    "?jackett_apikey=SECRETO&path=abc"
                ),
                "guid": "jackett-1",
                "indexer": "TrackerJackett (API)",
            }
        ]
    )
    report = sync_sources(
        db, client, jackett=jackett, limit=5, delay=0
    )
    assert report["jackett_results"] == 1
    assert report["inserted"] == 1
    assert jackett.searches[0][0] == "all"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    release = conn.execute("SELECT * FROM releases").fetchone()
    assert release["tracker"] == "TrackerJackett (API)"
    assert release["audio"] == "castellano"
    # La etiqueta "Spanish" del título manda sobre la inferencia del tracker.
    assert release["audio_confidence"] == "title"
    assert "SECRETO" not in release["download_url"]
    assert "jackett_apikey" not in release["download_url"]
    conn.close()


def test_jackett_does_not_duplicate_existing_title(tmp_path):
    from catalogo.bulk_sync import _upsert_release

    db = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,normalized_title,"
        "size_bytes,health) VALUES(?,?,?,?,?,?)",
        (
            asset_id,
            "TrackerJackett (API)",
            "Heart.of.the.Beast.2026.1080p.mkv Spanish",
            "heartofthebeast20261080pmkvspanish",
            1000,
            "unknown",
        ),
    )
    conn.commit()

    outcome = _upsert_release(
        conn,
        asset_id,
        "TrackerJackett (API)",
        {
            "title": "Heart.of.the.Beast.2026.1080p.mkv Spanish [TRACKERJACKETT]",
            "size": 1000,
            "seeders": 8,
            "peers": 0,
            "infoHash": None,
            "downloadUrl": "http://127.0.0.1:9117/dl/trackerjackett/?path=abc",
            "guid": "jackett-2",
        },
        "2026-11-19T12:00:00+00:00",
    )
    assert outcome == "updated"
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    conn.close()
