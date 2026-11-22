import sqlite3
from pathlib import Path

from catalogo.admission import admit
from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.public_sync import matches, sync_assets
from catalogo.resolver import Resolver

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeJackett:
    def __init__(self):
        self.calls = []

    def fetch_torrent(self, link):
        return b"d4:info4:teste"

    def search(self, indexer, query, *, media_type="movie", limit=50):
        self.calls.append((indexer, query, media_type))
        if indexer == "publico1":
            return [
                {
                    "indexer": "Publico1",
                    "title": "Encanto SPANISH BDRip x264",
                    "size": 2147483648,
                    "seeders": 1,
                    "peers": 2,
                    "infohash": "",
                    "link": (
                        "http://127.0.0.1:9117/dl/publico1/"
                        "?jackett_apikey=SECRET&path=abc&file=Encanto"
                    ),
                    "guid": "https://publico1.foo/encanto",
                }
            ]
        return []


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Encanto", 2021),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2 (API)", "Encanto.Private.mkv", 1000,
         "dead", "prowlarr", "private"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO play_targets(asset_id, release_id, source_kind) "
        "VALUES(?,?,?)",
        (asset_id, release_id, "torrent"),
    )
    conn.commit()
    conn.close()
    return db, asset_id, release_id


def test_public_fallback_inserts_sanitized_release(tmp_path):
    db, _asset_id, _release_id = seed_db(tmp_path)
    client = FakeJackett()
    report = sync_assets(
        str(db), client, key="movie:tmdb:1", indexers=["publico1"], delay=0
    )
    assert report["inserted"] == 1
    assert report["errors"] == []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM releases WHERE source='jackett'"
    ).fetchone()
    assert row["tracker"] == "Publico1"
    assert row["privacy"] == "public"
    assert row["health"] == "alive"
    assert "SECRET" not in row["download_url"]
    assert "jackett_apikey" not in row["download_url"]
    assert row["torrent_blob"] == b"d4:info4:teste"
    conn.close()


def test_resolver_falls_back_to_public_alive(tmp_path):
    db, _asset_id, _release_id = seed_db(tmp_path)
    sync_assets(
        str(db), FakeJackett(), key="movie:tmdb:1",
        indexers=["publico1"], delay=0,
    )
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "torrent"
    assert result.reason == "fallback_alive_release"
    assert result.health == "alive"
    assert result.tracker == "Publico1"


def test_public_policy_allows_admission_without_seed(tmp_path):
    db, _asset_id, _release_id = seed_db(tmp_path)
    sync_assets(
        str(db), FakeJackett(), key="movie:tmdb:1",
        indexers=["publico1"], delay=0,
    )
    upsert_policy(
        str(db),
        TrackerPolicy(
            "Publico1", min_seed_hours=0, target_ratio=0, max_active=2,
            allow_partial=True, keep_for_bonus=False, privacy="public",
        ),
    )
    result = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12,
    )
    assert result.decision.allowed
    assert result.intent_id is not None
    assert result.decision.seed_required_until is None


def test_matches_rejects_tmdb_mismatch():
    asset = {"title": "Dune", "year": 2021, "tmdb": 438631, "key": "movie:tmdb:438631"}
    result = {"title": "Planet Dune (2021) 1080p", "tmdbId": 881460}
    assert not matches(asset, result)
    assert matches(asset, {"title": "Dune (2021) 1080p", "tmdbId": 438631})
    assert matches(
        {"title": "Blade Runner 2049", "year": 2049, "tmdb": 335984,
         "key": "movie:tmdb:335984"},
        {"title": "Blade Runner 2049 (2017) 1080p", "tmdbId": 335984},
    )
