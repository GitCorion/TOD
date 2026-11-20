from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import connect, init_db
from catalogo.policy import (
    TrackerPolicy,
    can_remove,
    get_policy,
    list_policies,
    seed_required_until,
    upsert_policy,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_cannot_remove_before_min_seed_time():
    policy = TrackerPolicy("TrackerEspanol", min_seed_hours=72)
    started = (NOW - timedelta(hours=71)).isoformat()
    decision = can_remove(
        state="seeding", seed_started_at=started, ratio=5.0,
        policy=policy, now=NOW,
    )
    assert not decision.allowed
    assert decision.reason == "tiempo_minimo_de_seed"


def test_can_remove_after_min_seed_time():
    policy = TrackerPolicy("TrackerEspanol", min_seed_hours=72)
    started = (NOW - timedelta(hours=73)).isoformat()
    decision = can_remove(
        state="seeding", seed_started_at=started, ratio=0.1,
        policy=policy, now=NOW,
    )
    assert decision.allowed
    assert decision.reason == "politica_cumplida"


def test_ratio_blocks_removal():
    policy = TrackerPolicy("TrackerEspanol2", min_seed_hours=24, target_ratio=1.0)
    started = (NOW - timedelta(hours=48)).isoformat()
    decision = can_remove(
        state="seeding", seed_started_at=started, ratio=0.5,
        policy=policy, now=NOW,
    )
    assert not decision.allowed
    assert decision.reason == "ratio_insuficiente"


def test_incomplete_or_no_seed_start_blocks_removal():
    policy = TrackerPolicy("TrackerInternacional", min_seed_hours=0)
    assert not can_remove(
        state="downloading", seed_started_at=None, ratio=0, policy=policy,
        now=NOW,
    ).allowed
    assert not can_remove(
        state="seeding", seed_started_at=None, ratio=0, policy=policy,
        now=NOW,
    ).allowed


def test_seed_deadline():
    policy = TrackerPolicy("BTArg", min_seed_hours=72)
    started = "2026-11-16T00:00:00Z"
    deadline = seed_required_until(started, policy)
    assert deadline is not None
    assert deadline == datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)


def test_missing_policy_never_allows_removal():
    decision = can_remove(
        state="seeding", seed_started_at=NOW.isoformat(), ratio=99,
        policy=None, now=NOW,
    )
    assert not decision.allowed
    assert decision.reason == "sin_politica"


def test_upsert_and_read_policy(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    policy = TrackerPolicy(
        "TrackerEspanol2", min_seed_hours=72, target_ratio=1.0,
        max_active=3, allow_partial=False, notes="pendiente",
    )
    upsert_policy(db, policy)
    with connect(db) as conn:
        loaded = get_policy(conn, "TrackerEspanol2")
        assert loaded == policy
        assert get_policy(conn, "TrackerEspanol4") is None
    listed = list_policies(db)
    assert len(listed) == 1
    assert listed[0]["allow_partial"] == 0

