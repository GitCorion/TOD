import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.retention import evictable, finish_intent, status

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def setup(tmp_path, min_seed_hours=72, target_ratio=1.0):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol2", min_seed_hours=min_seed_hours,
            target_ratio=target_ratio, max_active=5, allow_partial=False,
        ),
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders,info_hash) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000, "alive", 10, "hash1"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.commit()
    conn.close()
    return db, asset_id, release_id


def add_intent(db, asset_id, release_id, started_hours_ago, info_hash="hash1"):
    started = (NOW - timedelta(hours=started_hours_ago)).isoformat()
    conn = sqlite3.connect(db)
    cursor = conn.execute(
        """
        INSERT INTO seed_commitments(asset_id, release_id, tracker, info_hash,
                                     size_bytes, state, seed_required_until,
                                     target_ratio, started_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            asset_id, release_id, "TrackerEspanol2", info_hash, 1000, "seeding",
            (NOW + timedelta(hours=72 - started_hours_ago)).isoformat(),
            1.0, started,
        ),
    )
    intent_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO torrents(info_hash, release_id, tracker, state,
                             seed_started_at, ratio)
        VALUES(?,?,?,?,?,?)
        """,
        (info_hash, release_id, "TrackerEspanol2", "seeding", started, 2.0),
    )
    conn.commit()
    conn.close()
    return intent_id


def test_status_blocks_before_seed_time(tmp_path):
    db, asset, release = setup(tmp_path)
    intent_id = add_intent(db, asset, release, 70)
    current = status(db, intent_id, now=NOW)
    assert current is not None
    assert not current.allowed
    assert current.reason == "tiempo_minimo_de_seed"

    result = finish_intent(db, intent_id, now=NOW)
    assert not result["closed"]
    assert result["reason"] == "tiempo_minimo_de_seed"


def test_finish_after_seed_time(tmp_path):
    db, asset, release = setup(tmp_path)
    intent_id = add_intent(db, asset, release, 73)
    current = status(db, intent_id, now=NOW)
    assert current.allowed

    result = finish_intent(db, intent_id, now=NOW)
    assert result["closed"]
    assert result["reason"] == "politica_cumplida"

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE id=?", (intent_id,)
    ).fetchone()[0] == "done"
    conn.close()


def test_ratio_blocks_even_after_time(tmp_path):
    db, asset, release = setup(tmp_path, target_ratio=2.0)
    intent_id = add_intent(db, asset, release, 200)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE torrents SET ratio=1.0 WHERE info_hash='hash1'")
    conn.commit()
    conn.close()
    current = status(db, intent_id, now=NOW)
    assert not current.allowed
    assert current.reason == "ratio_insuficiente"


def test_missing_policy_blocks(tmp_path):
    db, asset, release = setup(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM tracker_policies")
    conn.commit()
    conn.close()
    intent_id = add_intent(db, asset, release, 200)
    current = status(db, intent_id, now=NOW)
    assert not current.allowed
    assert current.reason == "sin_politica"


def test_evictable_only_lists_fulfilled(tmp_path):
    db, asset, release = setup(tmp_path)
    first = add_intent(db, asset, release, 73, info_hash="hash1")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders,info_hash) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset, "TrackerEspanol2", "Test2.mkv", 1000, "alive", 10, "hash2"),
    )
    second_release = conn.execute(
        "SELECT id FROM releases WHERE info_hash='hash2'"
    ).fetchone()[0]
    conn.commit()
    conn.close()
    second = add_intent(db, asset, second_release, 10, info_hash="hash2")
    rows = evictable(db, now=NOW)
    ids = {row["intent_id"] for row in rows}
    assert first in ids
    assert second not in ids
