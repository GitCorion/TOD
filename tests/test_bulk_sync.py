import sqlite3
from pathlib import Path

from catalogo.bulk_sync import (
    build_query,
    matches_asset,
    sync_sources,
)
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeProwlarr:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def indexers(self):
        caps = {
            "movieSearchParams": ["q", "tmdbId"],
            "tvSearchParams": ["q", "tvdbId"],
        }
        return [
            {"id": 1, "name": "TrackerEspanol (API)", "language": "es-ES",
             "capabilities": caps},
            {"id": 5, "name": "TrackerEspanol2 (API)", "language": "es-ES",
             "capabilities": caps},
            {"id": 7, "name": "TrackerDeportes (API)", "language": "es-MX",
             "enable": False, "capabilities": caps},
            {"id": 20, "name": "TrackerInternacional (API)", "language": "en-US",
             "capabilities": caps},
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


def test_same_info_hash_updates_instead_of_colliding(tmp_path):
    from catalogo.bulk_sync import _upsert_release

    db = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,normalized_title,"
        "size_bytes,info_hash,health) VALUES(?,?,?,?,?,?,?)",
        (
            asset_id,
            "Tracker",
            "Pelicula.2026.1080p.mkv",
            "pelicula20261080pmkv",
            1000,
            "abc123",
            "unknown",
        ),
    )
    conn.commit()

    outcome = _upsert_release(
        conn,
        asset_id,
        "Tracker",
        {
            "title": "Pelicula.2026.1080p.REPACK.mkv",
            "size": 1000,
            "seeders": 12,
            "peers": 1,
            "infoHash": "ABC123",
            "downloadUrl": "http://tracker/download",
            "guid": "duplicado",
        },
        "2026-11-19T12:00:00+00:00",
    )
    assert outcome == "updated"
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    row = conn.execute("SELECT title, seeders FROM releases").fetchone()
    assert row["title"] == "Pelicula.2026.1080p.REPACK.mkv"
    assert row["seeders"] == 12
    conn.close()


class PublicIndexerProwlarr(FakeProwlarr):
    def indexers(self):
        return [{"id": 1, "name": "TrackerEspanol (API)", "privacy": "public"}]


def test_prowlarr_privacy_is_kept(tmp_path):
    db = seed_db(tmp_path)
    sync_sources(db, PublicIndexerProwlarr([result()]), limit=5, delay=0)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT privacy, source FROM releases").fetchone()
    conn.close()
    assert row == ("public", "prowlarr")


def test_jackett_privacy_is_kept(tmp_path):
    db = seed_db(tmp_path)
    jackett = FakeJackett(
        [
            {
                "title": "Heart.of.the.Beast.2026.1080p.mkv Spanish",
                "size": 1000,
                "seeders": 5,
                "peers": 0,
                "infohash": "",
                "link": "http://127.0.0.1:9117/dl/dontorrent/?path=abc",
                "guid": "jackett-public",
                "indexer": "DonTorrent",
                "privacy": "public",
            }
        ]
    )
    sync_sources(db, FakeProwlarr([]), jackett=jackett, limit=5, delay=0)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT privacy, source FROM releases").fetchone()
    conn.close()
    assert row == ("public", "jackett")


def test_jackett_is_only_a_fallback(tmp_path):
    db = seed_db(tmp_path)
    jackett = FakeJackett(
        [
            {
                "title": "Heart.of.the.Beast.2026.1080p.mkv Spanish",
                "size": 1000,
                "seeders": 5,
                "peers": 0,
                "infohash": "",
                "link": "http://127.0.0.1:9117/dl/tracker/?path=abc",
                "guid": "jackett-fallback",
                "indexer": "TrackerJackett (API)",
                "privacy": "public",
            }
        ]
    )
    report = sync_sources(
        db, FakeProwlarr([result()]), jackett=jackett, limit=5, delay=0
    )
    assert report["inserted"] == 1
    # Prowlarr ya sirvió: no se paga el "all" de Jackett.
    assert jackett.searches == []


def test_blocked_trackers_are_not_queried(tmp_path):
    from catalogo.policy import TrackerPolicy, upsert_policy

    db = seed_db(tmp_path)
    upsert_policy(
        str(db), TrackerPolicy("TrackerEspanol (API)", enabled=False)
    )
    client = FakeProwlarr([result()])
    report = sync_sources(db, client, limit=5, delay=0)
    assert "TrackerEspanol (API)" in report["blocked_indexers"]
    assert client.searches
    assert all(1 not in ids for _, ids in client.searches)


def test_searches_spanish_and_original_titles(tmp_path):
    db = seed_db(tmp_path)
    client = FakeProwlarr([])
    sync_sources(db, client, limit=5, delay=0)
    queries = [query for query, _ in client.searches]
    assert any("corazón" in query for query in queries)
    assert any("Heart of the Beast" in query for query in queries)


class IdProwlarr(FakeProwlarr):
    def __init__(self):
        super().__init__([])
        self.id_calls = []

    def torznab_search(
        self, indexer_id, *, media_type="movie", query="", tmdb_id=None,
        tvdb_id=None, imdb_id=None, limit=50,
    ):
        self.id_calls.append((indexer_id, tmdb_id))
        return [
            {
                "title": "Dune Parte dos (2024) [HDO].mkv SPANiSH",
                "size": 1000,
                "seeders": 9,
                "peers": 1,
                "infoHash": "abc123",
                "downloadUrl": "http://127.0.0.1:9696/1/download?apikey=x",
                "infoUrl": "https://hd-olimpo.club/torrents/1",
                "guid": "portada-por-id",
                "tmdbId": 1263337,
                "category": "2000",
            }
        ]


def test_id_fallback_finds_a_release_with_another_title(tmp_path):
    db = seed_db(tmp_path)
    client = IdProwlarr()
    report = sync_sources(
        db, client, limit=5, delay=0, id_fallback=True
    )
    assert report["id_fallback"] is True
    assert report["inserted"] == 1
    assert client.id_calls
    assert all(tmdb == 1263337 for _, tmdb in client.id_calls)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT tracker, title FROM releases"
    ).fetchone()
    conn.close()
    assert row[0] in {item["name"] for item in client.indexers()}
    assert "Dune" in row[1]
