import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.seed_settings import load as load_seed
from catalogo.seed_settings import save as save_seed
from catalogo.seeding import normalize_completed

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


class FakeSeedingClient:
    def __init__(self, torrents):
        self._torrents = torrents
        self.calls = []

    def torrents(self, category=None):
        return self._torrents

    def set_force_start(self, info_hash, value=True):
        self.calls.append((info_hash, value))

    def resume(self, info_hash):
        self.resumed = getattr(self, "resumed", [])
        self.resumed.append(info_hash)


def seed_commitment(
    tmp_path,
    *,
    state="active",
    deadline=None,
    target=1.0,
    qbit_state="uploading",
    ratio=0.0,
    progress=1.0,
    completed_at=None,
):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state,started_at,seed_required_until,target_ratio) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            asset_id,
            None,
            "Tracker",
            "hash1",
            1000,
            state,
            (NOW - timedelta(hours=24)).isoformat(),
            None if deadline is False
            else (deadline or (NOW + timedelta(hours=48))).isoformat(),
            target,
        ),
    )
    conn.execute(
        "INSERT INTO torrents(info_hash,category,state,qbit_state,progress,"
        "ratio,completed_at) VALUES(?,?,?,?,?,?,?)",
        (
            "hash1", "ondemand", "seeding", qbit_state, progress, ratio,
            completed_at.isoformat() if completed_at else None,
        ),
    )
    conn.commit()
    conn.close()
    return db


def test_seed_settings_roundtrip(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    values = save_seed(str(db), min_hours=24, ratio=0.5, force=False)
    assert values == {"min_hours": 24, "ratio": 0.5, "force": False, "allow_delete": False}
    assert load_seed(str(db)) == values


def test_forces_seed_until_minimum(tmp_path):
    db = seed_commitment(tmp_path, qbit_state="uploading", ratio=0.1)
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0.1}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert [item["info_hash"] for item in report["forced"]] == ["hash1"]
    assert client.calls == [("hash1", True)]
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE id=1"
    ).fetchone()[0] == "seeding"
    conn.close()


def test_releases_force_when_minimum_is_met(tmp_path):
    db = seed_commitment(
        tmp_path,
        deadline=NOW - timedelta(hours=1),
        target=1.0,
        qbit_state="forcedUP",
        ratio=1.2,
    )
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "forcedUP", "progress": 1, "ratio": 1.2}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert [item["info_hash"] for item in report["released"]] == ["hash1"]
    assert client.calls == [("hash1", False)]
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE id=1"
    ).fetchone()[0] == "done"
    conn.close()


def test_does_not_force_when_setting_is_off(tmp_path):
    db = seed_commitment(tmp_path, qbit_state="uploading", ratio=0)
    save_seed(str(db), min_hours=72, ratio=1.0, force=False)
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert report["force"] is False
    assert report["forced"] == []
    assert client.calls == []


def test_incomplete_or_unknown_are_ignored(tmp_path):
    db = seed_commitment(tmp_path, progress=0.4)
    client = FakeSeedingClient(
        [
            {"hash": "hash1", "state": "downloading", "progress": 0.4},
            {"hash": "ajeno", "state": "forcedUP", "progress": 1},
        ]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert report["forced"] == []
    assert report["released"] == []
    assert client.calls == []


def test_matches_commitment_without_info_hash_by_release(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "Tracker", "Test", 1000, "alive", 10),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,category,state,qbit_state,"
        "progress,ratio) VALUES(?,?,?,?,?,?,?)",
        ("hash1", release_id, "ondemand", "seeding", "uploading", 1, 0),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state,seed_required_until,target_ratio) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            asset_id, release_id, "Tracker", None, 1000, "active",
            (NOW + timedelta(hours=48)).isoformat(), 1.0,
        ),
    )
    conn.commit()
    conn.close()
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert [item["info_hash"] for item in report["forced"]] == ["hash1"]
    assert client.calls == [("hash1", True)]


def test_cancels_stale_approved_without_torrent(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,tracker,size_bytes,state,"
        "approved_at,seed_required_until,target_ratio) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            asset_id, "Tracker", 1000, "approved",
            (NOW - timedelta(hours=1)).isoformat(),
            (NOW + timedelta(hours=48)).isoformat(), 1.0,
        ),
    )
    conn.commit()
    conn.close()
    report = normalize_completed(str(db), FakeSeedingClient([]), now=NOW)
    assert report["cancelled"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM seed_commitments"
    ).fetchone()[0] == "cancelled"
    conn.close()


def test_limit_is_respected(tmp_path):
    db = seed_commitment(tmp_path, qbit_state="uploading")
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0}]
    )
    report = normalize_completed(str(db), client, limit=1, dry_run=True, now=NOW)
    assert len(report["kept"]) == 1
    assert client.calls == []


def add_policy(db, hours=24, ratio=0.0):
    from catalogo.policy import TrackerPolicy, upsert_policy

    upsert_policy(
        str(db),
        TrackerPolicy(
            "Tracker", min_seed_hours=hours, target_ratio=ratio,
            max_active=2, allow_partial=False, privacy="private",
        ),
    )


def test_seed_clock_starts_when_download_completes(tmp_path):
    db = seed_commitment(
        tmp_path, deadline=False, completed_at=NOW - timedelta(hours=1),
        ratio=0.0,
    )
    add_policy(db, hours=24)
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0.0}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    # `started_at` es de hace 24 h (inicio de descarga), pero solo lleva
    # 1 h sembrando: no se puede soltar.
    assert report["released"] == []
    assert [item["info_hash"] for item in report["forced"]] == ["hash1"]
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT seed_started_at, seed_required_until FROM seed_commitments"
    ).fetchone()
    conn.close()
    assert row[0].startswith((NOW - timedelta(hours=1)).isoformat()[:19])
    assert row[1].startswith((NOW + timedelta(hours=23)).isoformat()[:19])


def test_paused_torrent_is_resumed_before_forcing(tmp_path):
    db = seed_commitment(tmp_path, qbit_state="pausedUP", ratio=0.0)
    add_policy(db, hours=24)
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "pausedUP", "progress": 1, "ratio": 0.0}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert client.resumed == ["hash1"]
    assert client.calls == [("hash1", True)]


def test_never_releases_without_a_known_seed_start(tmp_path):
    db = seed_commitment(tmp_path, deadline=False, ratio=0.0)
    add_policy(db, hours=24)
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0.0}]
    )
    report = normalize_completed(str(db), client, now=NOW)
    assert report["released"] == []
    assert report["forced"][0]["seed_started_at"] is not None


def test_tracker_verdict_is_stored_and_reported(tmp_path):
    db = seed_commitment(tmp_path, ratio=0.0)
    add_policy(db, hours=24)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "info_url) VALUES(1,?,?,?,?,?)",
        ("Tracker", "Test", 1000, "alive", "https://tracker.example/x"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "UPDATE seed_commitments SET release_id=?", (release_id,)
    )
    conn.commit()
    conn.close()
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0.0}]
    )
    client.trackers = lambda info_hash: [
        {"url": "https://tr.tracker.example/announce", "status": 4,
         "msg": "unregistered torrent"},
        {"url": "https://otro.example/announce", "status": 4,
         "msg": "IP banned"},
    ]
    report = normalize_completed(str(db), client, now=NOW)
    assert report["problems"][0]["verdict"] == "unregistered"
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT tracker_verdict, tracker_message, tracker_checked_at "
        "FROM seed_commitments"
    ).fetchone()
    conn.close()
    assert row[0] == "unregistered"
    assert "unregistered torrent" in row[1]
    assert row[2] is not None


def test_messages_from_other_trackers_are_ignored(tmp_path):
    db = seed_commitment(tmp_path, ratio=0.0)
    add_policy(db, hours=24)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "info_url) VALUES(1,?,?,?,?,?)",
        ("Tracker", "Test", 1000, "alive", "https://tracker.example/x"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute("UPDATE seed_commitments SET release_id=?", (release_id,))
    conn.commit()
    conn.close()
    client = FakeSeedingClient(
        [{"hash": "hash1", "state": "uploading", "progress": 1, "ratio": 0.0}]
    )
    client.trackers = lambda info_hash: [
        {"url": "https://publico.example/announce", "status": 4,
         "msg": "Torrent not registered: e698"},
        {"url": "udp://otro.example:6969/announce", "status": 4,
         "msg": "IP banned"},
    ]
    report = normalize_completed(str(db), client, now=NOW)
    assert report["problems"] == []
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT tracker_verdict FROM seed_commitments"
    ).fetchone()[0] is None
    conn.close()
