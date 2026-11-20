"""Estado de seed y cierre seguro de compromisos.

No borra ficheros ni torrents. Solo responde:
  - ¿este intent ya cumplió su tiempo/ratio?
  - ¿qué intents se pueden cerrar?
  - marca `done` únicamente si la política se cumple.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import connect
from .policy import can_remove, get_policy


@dataclass(frozen=True)
class SeedStatus:
    intent_id: int
    key: str
    tracker: str
    state: str
    seed_started_at: str | None
    ratio: float
    allowed: bool
    reason: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _intent(conn, intent_id: int):
    return conn.execute(
        """
        SELECT i.*, a.key
        FROM seed_commitments i
        JOIN media_items a ON a.id = i.asset_id
        WHERE i.id = ?
        """,
        (intent_id,),
    ).fetchone()


def _torrent_for(conn, intent):
    if intent["info_hash"]:
        row = conn.execute(
            "SELECT * FROM torrents WHERE info_hash = ? ORDER BY id DESC LIMIT 1",
            (intent["info_hash"],),
        ).fetchone()
        if row is not None:
            return row
    if intent["release_id"] is not None:
        return conn.execute(
            "SELECT * FROM torrents WHERE release_id = ? ORDER BY id DESC LIMIT 1",
            (intent["release_id"],),
        ).fetchone()
    return None


def status(
    db_path,
    intent_id: int,
    now: datetime | None = None,
    *,
    state: str | None = None,
    ratio: float | None = None,
    started: str | None = None,
) -> SeedStatus | None:
    with closing(connect(db_path)) as conn:
        intent = _intent(conn, intent_id)
        if intent is None:
            return None
        policy = get_policy(conn, intent["tracker"])
        torrent = _torrent_for(conn, intent)
        effective_state = state or intent["state"]
        effective_started = started or (
            (torrent["seed_started_at"] if torrent else None)
            or intent["started_at"]
        )
        effective_ratio = (
            ratio
            if ratio is not None
            else float(torrent["ratio"] or 0.0) if torrent else 0.0
        )
        decision = can_remove(
            state=effective_state,
            seed_started_at=effective_started,
            ratio=effective_ratio,
            policy=policy,
            now=now or _now(),
        )
        return SeedStatus(
            intent_id=intent["id"],
            key=intent["key"],
            tracker=intent["tracker"],
            state=effective_state,
            seed_started_at=effective_started,
            ratio=effective_ratio,
            allowed=decision.allowed,
            reason=decision.reason,
        )


def finish_intent(
    db_path,
    intent_id: int,
    now: datetime | None = None,
    *,
    state: str | None = None,
    ratio: float | None = None,
    started: str | None = None,
) -> dict:
    current = status(
        db_path, intent_id, now=now, state=state, ratio=ratio, started=started
    )
    if current is None:
        raise KeyError(f"intent no encontrado: {intent_id}")
    if not current.allowed:
        return {
            "intent_id": intent_id,
            "closed": False,
            "reason": current.reason,
        }
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE seed_commitments
            SET state = 'done',
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND state = 'seeding'
            """,
            (intent_id,),
        )
        conn.commit()
    return {"intent_id": intent_id, "closed": True, "reason": "politica_cumplida"}


def evictable(db_path, now: datetime | None = None) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id FROM seed_commitments WHERE state = 'seeding' ORDER BY id"
        ).fetchall()
    result = []
    for row in rows:
        current = status(db_path, row["id"], now=now)
        if current and current.allowed:
            result.append(
                {
                    "intent_id": current.intent_id,
                    "key": current.key,
                    "tracker": current.tracker,
                    "seed_started_at": current.seed_started_at,
                    "ratio": current.ratio,
                    "reason": current.reason,
                }
            )
    return result
