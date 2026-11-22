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


def default_policy(
    tracker: str,
    privacy: str | None = None,
    *,
    min_seed_hours: int | None = None,
    target_ratio: float | None = None,
) -> TrackerPolicy:
    """Política de arranque para un tracker sin regla configurada.

    Sin ella, cualquier descarga privada se bloquea con ``sin_politica`` y
    el play bajo demanda no funciona en una instalación recién creada. Los
    valores se pueden cambiar desde Configuración → Siembra.
    """
    public = (privacy or "").lower() == "public"
    hours = (
        0 if public
        else max(0, int(min_seed_hours if min_seed_hours is not None else 72))
    )
    ratio = (
        0.0 if public
        else max(
            0.0,
            float(target_ratio if target_ratio is not None else 1.0),
        )
    )
    return TrackerPolicy(
        tracker=tracker,
        min_seed_hours=hours,
        target_ratio=ratio,
        privacy="public" if public else "private",
        keep_for_bonus=True,
        notes="Política automática; editable en Configuración → Siembra.",
    )


def ensure_default_policy(
    conn: Any,
    tracker: str,
    privacy: str | None = None,
    *,
    min_seed_hours: int | None = None,
    target_ratio: float | None = None,
) -> TrackerPolicy:
    """Devuelve la política existente o crea una por defecto en la misma conexión."""
    existing = get_policy(conn, tracker)
    if existing is not None:
        return existing
    if min_seed_hours is None or target_ratio is None:
        from .seed_settings import from_conn

        values = from_conn(conn)
        min_seed_hours = values["min_hours"]
        target_ratio = values["ratio"]
    policy = default_policy(
        tracker,
        privacy,
        min_seed_hours=min_seed_hours,
        target_ratio=target_ratio,
    )
    conn.execute(
        """
        INSERT INTO tracker_policies(tracker, min_seed_hours, target_ratio,
                                     max_active, allow_partial, keep_for_bonus,
                                     privacy, enabled, abandon_under_percent,
                                     abandon_action, notes, updated_at)
        VALUES (?, ?, ?, NULL, 0, ?, ?, 1, 0, 'keep', ?,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(tracker) DO NOTHING
        """,
        (
            policy.tracker,
            policy.min_seed_hours,
            policy.target_ratio,
            1 if policy.keep_for_bonus else 0,
            policy.privacy,
            policy.notes,
        ),
    )
    return get_policy(conn, tracker) or policy


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
