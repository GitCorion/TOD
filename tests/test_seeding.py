import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.seeding import normalize_completed

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeSeedingClient:
    def __init__(self, torrents):
        self._torrents = torrents
        self.calls = []

    def torrents(self, category=None):
        return self._torrents

    def set_force_start(self, info_hash, value=True):
        self.calls.append((info_hash, value))


def seed_db(tmp_path, hashes):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for hash_ in hashes:
        conn.execute(
            "INSERT INTO torrents(info_hash,category,state,qbit_state,progress) "
            "VALUES(?,?,?,?,?)",
            (hash_, "ondemand", "seeding", "forcedUP", 1),
        )
    conn.commit()
    conn.close()
    return db


def test_dry_run_only_plans_completed_forced(tmp_path):
    db = seed_db(tmp_path, ["hash1", "hash2", "hash3"])
    client = FakeSeedingClient(
        [
            {"hash": "hash1", "state": "forcedUP", "progress": 1, "name": "ok"},
            {"hash": "hash2", "state": "forcedDL", "progress": 0.4, "name": "parcial"},
            {"hash": "hash3", "state": "uploading", "progress": 1, "name": "normal"},
        ]
    )
    report = normalize_completed(str(db), client, dry_run=True)
    assert [item["info_hash"] for item in report["planned"]] == ["hash1"]
    assert report["skipped_incomplete"] == 1
    assert client.calls == []


def test_unknown_torrent_is_ignored(tmp_path):
    db = seed_db(tmp_path, ["hash1"])
    client = FakeSeedingClient(
        [{"hash": "ajeno", "state": "forcedUP", "progress": 1}]
    )
    report = normalize_completed(str(db), client, dry_run=True)
    assert report["planned"] == []
    assert client.calls == []


def test_execute_removes_force_and_updates_state(tmp_path):
    db = seed_db(tmp_path, ["hash1"])
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "forcedUP", "progress": 1, "name": "ok"}]
    )
    report = normalize_completed(str(db), client, dry_run=False)
    assert report["normalized"][0]["info_hash"] == "hash1"
    assert client.calls == [("hash1", False)]
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT qbit_state FROM torrents WHERE info_hash='hash1'"
    ).fetchone()[0] == "normalizing"
    conn.close()


def test_limit_is_respected(tmp_path):
    db = seed_db(tmp_path, ["hash1", "hash2"])
    client = FakeSeedingClient(
        [
            {"hash": "hash1", "state": "forcedUP", "progress": 1},
            {"hash": "hash2", "state": "forcedUP", "progress": 1},
        ]
    )
    report = normalize_completed(str(db), client, limit=1, dry_run=True)
    assert len(report["planned"]) == 1


def test_error_is_reported(tmp_path):
    db = seed_db(tmp_path, ["hash1"])

    class Broken(FakeSeedingClient):
        def set_force_start(self, info_hash, value=True):
            raise RuntimeError("sin conexión")

    client = Broken(
        [{"hash": "hash1", "state": "forcedUP", "progress": 1}]
    )
    report = normalize_completed(str(db), client, dry_run=False)
    assert report["normalized"] == []
    assert report["errors"]
