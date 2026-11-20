import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.repair import repair_torrents

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeRead:
    def __init__(self, msg="Working"):
        self.msg = msg
        self.calls = []

    def trackers(self, info_hash):
        self.calls.append(info_hash)
        return [{"msg": self.msg, "status": 2}]


class FakeWrite:
    def __init__(self):
        self.rechecked = []
        self.resumed = []
        self.forced = []

    def recheck(self, info_hash):
        self.rechecked.append(info_hash)

    def resume(self, info_hash):
        self.resumed.append(info_hash)

    def set_force_start(self, info_hash, value=True):
        self.forced.append((info_hash, value))


def seed(tmp_path, *, commitment=True):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 100, "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,qbit_state,"
        "category,progress) VALUES(?,?,?,?,?,?,?)",
        ("roto", release_id, "TrackerEspanol2", "error", "error", "ondemand", 0),
    )
    if commitment:
        conn.execute(
            "INSERT INTO seed_commitments(asset_id,release_id,tracker,"
            "info_hash,size_bytes,state) VALUES(?,?,?,?,?,?)",
            (asset_id, release_id, "TrackerEspanol2", "roto", 100, "active"),
        )
    conn.commit()
    conn.close()
    return db


def test_repair_rechecks_error_torrent(tmp_path):
    db = seed(tmp_path)
    read, write = FakeRead(), FakeWrite()
    report = repair_torrents(db, read, write, limit=5, dry_run=False)
    assert report["repaired"] == [{"info_hash": "roto"}]
    assert write.rechecked == ["roto"]
    assert write.resumed == ["roto"]
    assert write.forced == [("roto", True)]


def test_repair_skips_broken_tracker(tmp_path):
    db = seed(tmp_path)
    write = FakeWrite()
    report = repair_torrents(
        db, FakeRead("torrent not found"), write, limit=5, dry_run=False
    )
    assert report["skipped"][0]["reason"] == "tracker_roto"
    assert write.rechecked == []


def test_repair_skips_without_commitment(tmp_path):
    db = seed(tmp_path, commitment=False)
    write = FakeWrite()
    report = repair_torrents(db, FakeRead(), write, limit=5, dry_run=False)
    assert report["skipped"][0]["reason"] == "sin_compromiso"
    assert write.rechecked == []


def test_repair_dry_run_does_not_touch(tmp_path):
    db = seed(tmp_path)
    write = FakeWrite()
    report = repair_torrents(db, FakeRead(), write, limit=5, dry_run=True)
    assert report["planned"] == [{"info_hash": "roto"}]
    assert write.rechecked == []
