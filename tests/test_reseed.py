import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.reseed import reseed_pending

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
TORRENT = b"d4:info4:teste"
KEY = "movie:tmdb:300"


class FakeQbit:
    def __init__(self):
        self.torrents_list = []
        self.added = []
        self.priorities = []

    def torrents(self, category=None):
        return list(self.torrents_list)

    def files(self, info_hash):
        return [
            {"index": 0, "name": "Test.mkv", "size": 1000,
             "progress": 1, "priority": 1}
        ]

    def add_torrent(self, torrent_bytes, **kwargs):
        self.added.append(kwargs)
        self.torrents_list.append({"hash": "nuevo", "state": "pausedDL"})
        return "Ok."

    def set_file_priorities(self, info_hash, priorities):
        self.priorities.append((info_hash, priorities))

    def resume(self, info_hash):
        for torrent in self.torrents_list:
            if torrent.get("hash") == info_hash:
                torrent["state"] = "downloading"

    def set_force_start(self, info_hash, value=True):
        pass

    def pause(self, info_hash):
        pass

    def delete(self, info_hash, delete_files=True):
        pass


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year) "
        "VALUES(?,?,?,?,?,?)",
        (KEY, KEY, "movie", 300, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "source,privacy,torrent_blob) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000, "alive",
         "prowlarr", "private", TORRENT),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,"
        "size_bytes,state,reason) VALUES(?,?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", 1000, "approved",
         "reseed_early_removal"),
    )
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol2", min_seed_hours=120, target_ratio=0, max_active=2,
            allow_partial=False, keep_for_bonus=True, privacy="private",
        ),
    )
    return db, asset_id, release_id


def test_reseed_dry_run_lists_pending(tmp_path):
    db, _, _ = seed(tmp_path)
    client = FakeQbit()
    report = reseed_pending(db, client, limit=5, dry_run=True, now=NOW)
    assert report["checked"] == 1
    assert report["planned"][0]["key"] == KEY
    assert report["reseeded"] == []
    assert client.added == []


def test_reseed_readds_torrent_and_reactivates_commitment(tmp_path):
    db, asset_id, release_id = seed(tmp_path)
    client = FakeQbit()
    report = reseed_pending(db, client, limit=5, dry_run=False, now=NOW)
    assert report["reseeded"], report
    assert client.added
    assert client.added[0]["category"] == "ondemand"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    commitment = conn.execute(
        "SELECT state, started_at FROM seed_commitments WHERE id = ("
        "SELECT id FROM seed_commitments WHERE release_id=?)",
        (release_id,),
    ).fetchone()
    assert commitment["state"] == "active"
    assert commitment["started_at"] is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM torrents"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT status FROM media_items WHERE id=?", (asset_id,)
    ).fetchone()["status"] == "DOWNLOADING_LOCAL"
    conn.close()
