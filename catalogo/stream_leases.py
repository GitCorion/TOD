"""Leases de reproducción.

Cada petición de streaming renueva un lease. El worker de movimiento a
archivo no mueve un activo si tiene un lease reciente.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone

from .db import connect


def _now() -> datetime:
    return datetime.now(timezone.utc)


def touch(
    db_path: str,
    media_id: str,
    session_id: str,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO stream_leases(media_id, session_id, client_ip,
                                      user_agent, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(media_id, session_id) DO UPDATE SET
                client_ip = excluded.client_ip,
                user_agent = excluded.user_agent,
                last_seen = excluded.last_seen
            """,
            (media_id, session_id, client_ip, user_agent, current.isoformat()),
        )
        conn.commit()


def active(
    db_path: str,
    media_id: str,
    *,
    grace_seconds: int = 120,
    now: datetime | None = None,
) -> bool:
    current = now or _now()
    threshold = (current - timedelta(seconds=max(0, grace_seconds))).isoformat()
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM stream_leases
            WHERE media_id = ? AND last_seen >= ?
            LIMIT 1
            """,
            (media_id, threshold),
        ).fetchone()
    return row is not None


def release(db_path: str, media_id: str, session_id: str) -> int:
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM stream_leases WHERE media_id = ? AND session_id = ?",
            (media_id, session_id),
        )
        conn.commit()
    return cur.rowcount


def cleanup(db_path: str, *, before_seconds: int = 3600,
            now: datetime | None = None) -> int:
    current = now or _now()
    threshold = (current - timedelta(seconds=before_seconds)).isoformat()
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM stream_leases WHERE last_seen < ?", (threshold,)
        )
        conn.commit()
    return cur.rowcount
