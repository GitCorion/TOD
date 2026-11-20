import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.tracker_block import set_tracker_blocked

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    for title in ("a.mkv", "b.mkv"):
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
            "VALUES(?,?,?,?,?,?)",
            (asset_id, "TrackerJackett (API)", title, 1000, "alive", 5),
        )
    conn.commit()
    conn.close()
    upsert_policy(str(db), TrackerPolicy("TrackerJackett (API)", min_seed_hours=120))
    return db


def test_block_and_unblock_tracker(tmp_path):
    db = seed(tmp_path)
    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=True)
    assert report["releases"] == 2
    assert report["policy"] == "disabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='blocked'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 0
    conn.close()

    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=False)
    assert report["releases"] == 2
    assert report["policy"] == "enabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='unknown'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 1
    conn.close()


def test_block_dry_run_changes_nothing(tmp_path):
    db = seed(tmp_path)
    report = set_tracker_blocked(
        str(db), "TrackerJackett (API)", blocked=True, dry_run=True
    )
    assert report["releases"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='blocked'"
    ).fetchone()[0] == 0
    conn.close()
