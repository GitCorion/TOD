"""Política de seed por tracker.

No consulta nada. Codifica la regla que evita hit-and-run:
un torrent no se puede borrar hasta cumplir tiempo mínimo y/o ratio.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect


@dataclass(frozen=True)
class TrackerPolicy:
    tracker: str
    min_seed_hours: int = 0
    target_ratio: float = 0.0
    max_active: int | None = None
    allow_partial: bool = False
    keep_for_bonus: bool = True
    privacy: str = "private"
    enabled: bool = True
    abandon_under_percent: int = 0
    abandon_action: str = "keep"
    notes: str | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def seed_required_until(
    seed_started_at: str | None, policy: TrackerPolicy
) -> datetime | None:
    started = parse_time(seed_started_at)
    if started is None:
        return None
    return started + timedelta(hours=max(0, policy.min_seed_hours))


def can_remove(
    *,
    state: str,
    seed_started_at: str | None,
    ratio: float | None,
    policy: TrackerPolicy | None,
    now: datetime | None = None,
) -> Decision:
    """Decide si se puede borrar un torrent sin incumplir la política."""
    if policy is None:
        return Decision(False, "sin_politica")
    if state not in ("complete", "seeding"):
        return Decision(False, "no_esta_sembrando")
    started = parse_time(seed_started_at)
    if started is None:
        return Decision(False, "sin_inicio_de_seed")

    current = now or _utcnow()
    deadline = started + timedelta(hours=max(0, policy.min_seed_hours))
    if current < deadline:
        return Decision(False, "tiempo_minimo_de_seed")

    if policy.target_ratio > 0 and (ratio or 0.0) < policy.target_ratio:
        return Decision(False, "ratio_insuficiente")

    return Decision(True, "politica_cumplida")


def get_policy(conn: Any, tracker: str) -> TrackerPolicy | None:
    row = conn.execute(
        "SELECT * FROM tracker_policies WHERE tracker = ?", (tracker,)
    ).fetchone()
    if row is None:
        return None
    return TrackerPolicy(
        tracker=row["tracker"],
        min_seed_hours=row["min_seed_hours"] or 0,
        target_ratio=row["target_ratio"] or 0.0,
        max_active=row["max_active"],
        allow_partial=bool(row["allow_partial"]),
        keep_for_bonus=bool(row["keep_for_bonus"]),
        privacy=row["privacy"],
        enabled=bool(row["enabled"]),
        abandon_under_percent=int(row["abandon_under_percent"] or 0),
        abandon_action=row["abandon_action"] or "keep",
        notes=row["notes"],
    )


def upsert_policy(db_path: str, policy: TrackerPolicy) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO tracker_policies(tracker, min_seed_hours, target_ratio,
                                         max_active, allow_partial, keep_for_bonus,
                                         privacy, enabled, abandon_under_percent,
                                         abandon_action, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(tracker) DO UPDATE SET
                min_seed_hours = excluded.min_seed_hours,
                target_ratio = excluded.target_ratio,
                max_active = excluded.max_active,
                allow_partial = excluded.allow_partial,
                keep_for_bonus = excluded.keep_for_bonus,
                privacy = excluded.privacy,
                enabled = excluded.enabled,
                abandon_under_percent = excluded.abandon_under_percent,
                abandon_action = excluded.abandon_action,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                policy.tracker,
                policy.min_seed_hours,
                policy.target_ratio,
                policy.max_active,
                1 if policy.allow_partial else 0,
                1 if policy.keep_for_bonus else 0,
                policy.privacy,
                1 if policy.enabled else 0,
                policy.abandon_under_percent,
                policy.abandon_action,
                policy.notes,
            ),
        )
        conn.commit()


def list_policies(db_path: str) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM tracker_policies ORDER BY tracker"
        ).fetchall()
    return [dict(row) for row in rows]
