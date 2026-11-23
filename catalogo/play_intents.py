"""Intenciones de reproducción.

Se separan del `seed_commitment`. Una intención es efímera: confirma que la
petición viene de un play, no de un escaneo o un probe.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(
    db_path: str,
    media_id: str,
    *,
    source: str = "range",
    ttl_minutes: int = 5,
    client_ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    expires = current + timedelta(minutes=max(1, ttl_minutes))
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM play_intents
            WHERE media_id = ? AND consumed_at IS NULL AND expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (media_id, current.isoformat()),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            """
            INSERT INTO play_intents(media_id, source, client_ip, user_agent,
                                     created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                source,
                client_ip,
                user_agent,
                current.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM play_intents WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        )


def active(
    db_path: str, media_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    current = now or _now()
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM play_intents
            WHERE media_id = ? AND consumed_at IS NULL AND expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (media_id, current.isoformat()),
        ).fetchone()
    return dict(row) if row else None


def consume(
    db_path: str, intent_id: int, *, now: datetime | None = None
) -> bool:
    current = now or _now()
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            """
            UPDATE play_intents SET consumed_at = ?
            WHERE id = ? AND consumed_at IS NULL
            """,
            (current.isoformat(), intent_id),
        )
        conn.commit()
    return cur.rowcount > 0


def ready(
    db_path: str, intent_id: int, *, now: datetime | None = None
) -> None:
    """Marca el momento en que el stream está listo para enviar datos."""
    current = now or _now()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE play_intents SET ready_at = COALESCE(ready_at, ?) "
            "WHERE id = ?",
            (current.isoformat(), int(intent_id)),
        )
        conn.commit()


def first_byte(
    db_path: str, intent_id: int, *, now: datetime | None = None
) -> None:
    """Marca el primer byte enviado; no pisa una marca anterior."""
    current = now or _now()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE play_intents SET first_byte_at = COALESCE(first_byte_at, ?) "
            "WHERE id = ?",
            (current.isoformat(), int(intent_id)),
        )
        conn.commit()


def cleanup(db_path: str, *, now: datetime | None = None) -> int:
    current = now or _now()
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM play_intents WHERE expires_at <= ?", (current.isoformat(),)
        )
        conn.commit()
    return cur.rowcount
