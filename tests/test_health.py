import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.health import (
    apply_health,
    classify_health,
    pending_releases,
    publishable,
    release_rank,
    summary,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_local_is_publishable_without_health():
    assert publishable(source_kind="library").ok
    assert publishable(source_kind="local_seed").ok


def test_unavailable_is_never_publishable():
    decision = publishable(source_kind="unavailable")
    assert not decision.ok
    assert decision.reason == "unavailable"


def test_torrent_requires_seeders_when_configured():
    assert publishable(source_kind="torrent", seeders=5).ok
    decision = publishable(source_kind="torrent", seeders=0)
    assert not decision.ok
    assert decision.reason == "sin_peers"


def test_torrent_health_gate_when_required():
    decision = publishable(
        source_kind="torrent", health="unknown", seeders=50,
        require_health=True,
    )
    assert not decision.ok
    assert decision.reason == "health_unknown"
    assert publishable(
        source_kind="torrent", health="alive", seeders=50,
        require_health=True,
    ).ok


def test_release_rank_prefers_castellano_then_quality_then_seeders():
    castellano_1080 = release_rank(
        audio="castellano", quality="1080p", seeders=10, size_bytes=3_000_000_000
    )
    unknown_4k = release_rank(
        audio="unknown", quality="4K", seeders=100, size_bytes=20_000_000_000
    )
    assert castellano_1080 > unknown_4k


def test_release_rank_penalizes_oversize():
    small = release_rank(
        audio="castellano", quality="1080p", seeders=10, size_bytes=3_000_000_000,
        max_size_bytes=8_000_000_000,
    )
    huge = release_rank(
        audio="castellano", quality="1080p", seeders=100, size_bytes=90_000_000_000,
        max_size_bytes=8_000_000_000,
    )
    assert small > huge


def test_classify_health():
    assert classify_health(
        seeders=0, peers=0, checked_at=None, now=NOW
    ) == "unknown"
    assert classify_health(
        seeders=0, peers=0, checked_at=NOW.isoformat(), now=NOW
    ) == "dead"
    assert classify_health(
        seeders=5, peers=0, checked_at=NOW.isoformat(), now=NOW
    ) == "alive"
    assert classify_health(
        seeders=0, peers=2, checked_at=NOW.isoformat(), now=NOW
    ) == "alive"
    assert classify_health(
        seeders=5, peers=0,
        checked_at=(NOW - timedelta(hours=25)).isoformat(), now=NOW,
    ) == "stale"
    assert classify_health(
        seeders=0, peers=0, checked_at=NOW.isoformat(), now=NOW,
        had_result=False,
    ) == "dead"


def test_apply_health_and_summary(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title) VALUES(?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.commit()
    conn.close()

    result = apply_health(db, release_id, seeders=10, peers=2)
    assert result["health"] == "alive"
    assert summary(db)["alive"] == 1

    apply_health(db, release_id, seeders=0, peers=0, health="blocked")
    assert summary(db)["by_health"]["blocked"] == 1
    apply_health(db, release_id, seeders=0, peers=0, health="stale")
    assert pending_releases(db)[0]["id"] == release_id
