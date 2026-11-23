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
from pathlib import Path
from typing import Any

from .db import connect
from .policy import can_remove, get_policy, parse_time
from .seed_settings import load as load_seed_settings


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
    asset_id: int | None = None
    library_ready: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _intent(conn, intent_id: int):
    return conn.execute(
        """
        SELECT i.*, a.key, a.id AS asset_id
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
        ready = (
            _library_ready_conn(conn, intent["asset_id"])
            if intent["asset_id"] else False
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
            asset_id=intent["asset_id"],
            library_ready=ready,
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


def _library_ready_conn(conn: Any, asset_id: int) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM library_files lf
        JOIN media_items m ON m.id = ?
        WHERE lf.kind = m.kind AND lf.tmdb = m.tmdb
          AND (m.kind = 'movie'
               OR (lf.season = m.season AND lf.episode = m.episode))
        LIMIT 1
        """,
        (int(asset_id),),
    ).fetchone() is not None


def library_ready(asset_id: int, *, db_path: str | Path) -> bool:
    """¿El título ya está copiado en la biblioteca de Radarr/Sonarr?

    Se apoya en `library_files` (el índice escaneado), no en la cola del
    *arr: un `hasFile` sin fichero real no protegería nada.
    """
    with closing(connect(db_path)) as conn:
        return _library_ready_conn(conn, asset_id)


def evictable(db_path, now: datetime | None = None) -> list[dict]:
    current = now or _now()
    seed = load_seed_settings(db_path)
    keep_library = seed["keep_until_library"]
    keep_max = seed["keep_max_hours"]
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id FROM seed_commitments "
            "WHERE state IN ('seeding', 'done') ORDER BY id"
        ).fetchall()
    result = []
    for row in rows:
        entry = status(db_path, row["id"], now=current)
        if entry is None:
            continue
        if entry.state != "done" and not entry.allowed:
            continue
        started = parse_time(entry.seed_started_at)
        age_hours = (
            (current - started).total_seconds() / 3600
            if started is not None
            else 0.0
        )
        waited_out = keep_max > 0 and age_hours >= keep_max
        if keep_library and not entry.library_ready and not waited_out:
            # 0 = sin límite: esperar a la biblioteca, no borrar antes.
            continue
        reason = entry.reason
        if entry.state == "done":
            reason = (
                "en_biblioteca" if entry.library_ready else "espera_agotada"
            )
        result.append(
            {
                "intent_id": entry.intent_id,
                "key": entry.key,
                "tracker": entry.tracker,
                "seed_started_at": entry.seed_started_at,
                "ratio": entry.ratio,
                "reason": reason,
                "library_ready": entry.library_ready,
            }
        )
    return result
