import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.admission import (
    AdmissionRequest,
    admit,
    cancel_intent,
    decide,
    list_intents,
)
from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def policy(**overrides):
    base = dict(
        tracker="TrackerEspanol2",
        min_seed_hours=72,
        target_ratio=1.0,
        max_active=2,
        allow_partial=False,
    )
    base.update(overrides)
    return TrackerPolicy(**base)


def request(**overrides):
    base = dict(
        asset_key="movie:tmdb:1",
        release_id=1,
        tracker="TrackerEspanol2",
        size_bytes=1000,
        health="alive",
        seeders=10,
        policy=policy(),
        active_for_tracker=0,
        already_active=False,
        disk_free_bytes=10**12,
        reserve_bytes=0,
        require_health=True,
        min_seeders=1,
        now=NOW,
    )
    base.update(overrides)
    return AdmissionRequest(**base)


def test_decision_allows_and_sets_seed_deadline():
    decision = decide(request())
    assert decision.allowed
    assert decision.reason == "admitido"
    assert decision.seed_required_until == NOW + timedelta(hours=72)


def test_decision_blocks_without_policy():
    decision = decide(request(policy=None))
    assert not decision.allowed
    assert decision.reason == "sin_politica"


def test_decision_blocks_unknown_health():
    decision = decide(request(health="unknown"))
    assert not decision.allowed
    assert decision.reason == "health_unknown"
    assert decide(request(health="unknown", require_health=False)).allowed


def test_decision_blocks_without_peers():
    decision = decide(request(seeders=0))
    assert not decision.allowed
    assert decision.reason == "sin_peers"


def test_decision_blocks_without_space():
    decision = decide(
        request(size_bytes=1000, reserve_bytes=500, disk_free_bytes=1000)
    )
    assert not decision.allowed
    assert decision.reason == "sin_espacio"


def test_decision_accounts_for_already_reserved_space():
    decision = decide(
        request(
            size_bytes=500, reserve_bytes=0, disk_free_bytes=1000,
            reserved_bytes=600,
        )
    )
    assert not decision.allowed
    assert decision.reason == "sin_espacio"
    assert decision.available_bytes == 400


def test_decision_blocks_max_active():
    decision = decide(request(active_for_tracker=2))
    assert not decision.allowed
    assert decision.reason == "max_active"


def test_decision_blocks_policy_without_requirement():
    decision = decide(
        request(policy=policy(min_seed_hours=0, target_ratio=0))
    )
    assert not decision.allowed
    assert decision.reason == "politica_sin_requisito"


def test_decision_blocks_duplicate():
    decision = decide(request(already_active=True))
    assert not decision.allowed
    assert decision.reason == "ya_en_curso"


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    upsert_policy(str(db), policy())
    conn = sqlite3.connect(db)
    for n in (1, 2):
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
            (f"movie:tmdb:{n}", "movie", n, f"Test {n}", 2020),
        )
        asset_id = conn.execute(
            "SELECT id FROM media_items WHERE key=?", (f"movie:tmdb:{n}",)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders,info_hash) "
            "VALUES(?,?,?,?,?,?,?)",
            (asset_id, "TrackerEspanol2", f"Test.{n}.mkv", 1000, "alive", 10, f"hash{n}"),
        )
    conn.commit()
    conn.close()
    return db


def test_admit_persists_commitment_and_blocks_duplicate(tmp_path):
    db = seed_db(tmp_path)
    result = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert result.decision.allowed
    assert result.intent_id is not None
    assert result.decision.seed_required_until == NOW + timedelta(hours=72)

    intents = list_intents(db)
    assert len(intents) == 1
    assert intents[0]["state"] == "approved"
    assert intents[0]["target_ratio"] == 1.0
    assert intents[0]["tracker"] == "TrackerEspanol2"

    again = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert not again.decision.allowed
    assert again.decision.reason == "ya_en_curso"
    assert again.intent_id is None


def test_cancel_allows_new_commitment(tmp_path):
    db = seed_db(tmp_path)
    first = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    cancel_intent(db, first.intent_id, "prueba")
    second = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert second.decision.allowed
    assert second.intent_id != first.intent_id


def test_max_active_applies_across_releases(tmp_path):
    db = seed_db(tmp_path)
    one = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    two = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert one.decision.allowed
    assert two.decision.allowed
    # max_active=2: el tercero debe bloquearse.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:3", "movie", 3, "Test 3", 2020),
    )
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:3'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders,info_hash) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.3.mkv", 1000, "alive", 10, "hash3"),
    )
    conn.commit()
    conn.close()

    three = admit(
        db, "movie:tmdb:3", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert not three.decision.allowed
    assert three.decision.reason == "max_active"


def test_admit_blocks_without_space(tmp_path):
    db = seed_db(tmp_path)
    result = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=500, now=NOW,
    )
    assert not result.decision.allowed
    assert result.decision.reason == "sin_espacio"
    assert result.intent_id is None


def test_admit_does_not_overcommit_reserved_space(tmp_path):
    db = seed_db(tmp_path)
    one = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=1500, now=NOW,
    )
    assert one.decision.allowed
    two = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=1500, now=NOW,
    )
    assert not two.decision.allowed
    assert two.decision.reason == "sin_espacio"
    assert two.decision.available_bytes == 500
    assert two.intent_id is None


def test_existing_torrent_blocks_duplicate_without_intent(tmp_path):
    db = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, tracker, state, category) "
        "VALUES(?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "seeding", "ondemand"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:1", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert not result.decision.allowed
    assert result.decision.reason == "ya_en_curso"


def test_existing_torrent_counts_for_max_active(tmp_path):
    db = seed_db(tmp_path)
    upsert_policy(
        str(db),
        policy(max_active=1),
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, tracker, state, category) "
        "VALUES(?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "downloading", "ondemand"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert not result.decision.allowed
    assert result.decision.reason == "max_active"


def test_intent_and_torrent_are_not_double_counted(tmp_path):
    db = seed_db(tmp_path)
    upsert_policy(str(db), policy(max_active=2))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO seed_commitments(asset_id, release_id, tracker, info_hash, "
        "size_bytes, state) VALUES(?,?,?,?,?,?)",
        (1, 1, "TrackerEspanol2", "hash1", 1000, "active"),
    )
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, tracker, state, category) "
        "VALUES(?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "downloading", "ondemand"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert result.decision.allowed


def test_existing_seed_from_another_category_does_not_consume_max_active(tmp_path):
    db = seed_db(tmp_path)
    upsert_policy(str(db), policy(max_active=1))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, tracker, state, category) "
        "VALUES(?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "seeding", "radarr"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert result.decision.allowed


def test_public_policy_without_seed_requirement_is_allowed():
    decision = decide(
        request(
            policy=policy(
                tracker="Publico1", min_seed_hours=0, target_ratio=0,
                privacy="public", allow_partial=True,
            )
        )
    )
    assert decision.allowed
    assert decision.seed_required_until is None


def test_oversized_release_is_not_hard_blocked():
    decision = decide(request(size_bytes=1000, max_size_bytes=500))
    assert decision.allowed


def test_seeding_commitment_does_not_consume_max_active(tmp_path):
    db = seed_db(tmp_path)
    upsert_policy(str(db), policy(max_active=1))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO seed_commitments(asset_id, release_id, tracker, info_hash, "
        "size_bytes, state, seed_required_until) "
        "VALUES(?,?,?,?,?,?,?)",
        (1, 1, "TrackerEspanol2", "old", 1000, "active", "2030-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
    )
    assert result.decision.allowed


def test_completed_active_commitment_does_not_reserve_disk(tmp_path):
    db = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress,qbit_state) VALUES(?,?,?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "seeding", "ondemand", 1.0, "stalledUP"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id, release_id, tracker, info_hash, "
        "size_bytes, state) VALUES(?,?,?,?,?,?)",
        (1, 1, "TrackerEspanol2", "hash1", 200 * 1024**3, "active"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=1024**3, now=NOW,
    )
    assert result.decision.allowed


def test_active_for_tracker_override_ignores_queued_or_checking(tmp_path):
    db = seed_db(tmp_path)
    upsert_policy(str(db), policy(max_active=1))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress,qbit_state) VALUES(?,?,?,?,?,?,?)",
        ("hash1", 1, "TrackerEspanol2", "downloading", "ondemand", 0.0,
         "downloading"),
    )
    conn.commit()
    conn.close()
    result = admit(
        db, "movie:tmdb:2", data_path=tmp_path,
        reserve_bytes=0, disk_free_override=10**12, now=NOW,
        active_for_tracker_override=0,
    )
    assert result.decision.allowed
