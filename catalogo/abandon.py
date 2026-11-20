"""Abandona descargas que se quedaron por debajo del umbral sin verse.

Un torrent añadido bajo demanda que lleva un rato descargando y no llega al
porcentaje configurado en su tracker, y que además no tiene ninguna lease de
reproducción reciente, se considera abandonado:

  - públicos: se pausa o se borra (con ficheros) según la política.
  - privados: por defecto solo se pausa. Borrar una descarga parcial puede
    contar como hit-and-run; hace falta `allow_private_delete=True` para
    permitirlo expresamente.

El módulo no decide el porcentaje: lo lee de `tracker_policies`. Un valor 0
desactiva el abandono para ese tracker.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .db import connect
from .policy import parse_time
from .stream_leases import active as stream_active

ONDEMAND_CATEGORY = "ondemand"
INCOMPLETE_STATES = ("downloading", "waiting", "paused")


class AbandonClient(Protocol):
    def pause(self, info_hash: str) -> None: ...

    def delete(self, info_hash: str, *, delete_files: bool = False) -> None: ...


@dataclass(frozen=True)
class Candidate:
    torrent_id: int
    info_hash: str
    progress: float
    qbit_state: str | None
    state: str
    tracker: str | None
    privacy: str | None
    under_percent: int
    action: str
    keep_for_bonus: bool
    media_ids: tuple[str, ...]
    added_at: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(candidate: Candidate, now: datetime) -> float | None:
    added = parse_time(candidate.added_at)
    if added is None:
        return None
    return (now - added).total_seconds()


def _media_ids(db_path: str, torrent_id: int) -> tuple[str, ...]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.media_id
            FROM torrent_files f
            JOIN media_items m ON m.id = f.asset_id
            WHERE f.torrent_id = ?
            """,
            (torrent_id,),
        ).fetchall()
    return tuple(row["media_id"] for row in rows if row["media_id"])


def _candidates(db_path: str, limit: int) -> list[Candidate]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT t.id AS torrent_id, t.info_hash, t.progress, t.qbit_state,
                   t.state, t.added_at, r.tracker, r.privacy,
                   p.abandon_under_percent, p.abandon_action, p.keep_for_bonus
            FROM torrents t
            LEFT JOIN releases r ON r.id = t.release_id
            LEFT JOIN tracker_policies p ON p.tracker = r.tracker
            WHERE t.category = ?
              AND t.progress < 1
              AND t.state IN (?, ?, ?)
              AND lower(COALESCE(t.qbit_state, '')) NOT IN ('moving', 'pausedup',
                                                           'pauseddl')
            ORDER BY t.id
            LIMIT ?
            """,
            (ONDEMAND_CATEGORY, *INCOMPLETE_STATES, limit),
        ).fetchall()
    return [
        Candidate(
            torrent_id=int(row["torrent_id"]),
            info_hash=row["info_hash"],
            progress=float(row["progress"] or 0),
            qbit_state=row["qbit_state"],
            state=row["state"],
            tracker=row["tracker"],
            privacy=row["privacy"],
            under_percent=int(row["abandon_under_percent"] or 0),
            action=row["abandon_action"] or "keep",
            keep_for_bonus=bool(row["keep_for_bonus"]),
            media_ids=_media_ids(db_path, int(row["torrent_id"])),
            added_at=row["added_at"],
        )
        for row in rows
    ]


def _mark_paused(db_path: str, torrent_id: int) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE torrents SET state = 'paused', qbit_state = 'paused', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (torrent_id,),
        )
        conn.commit()


def _mark_deleted(db_path: str, torrent_id: int) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE torrents SET state = 'removed', qbit_state = 'removed', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (torrent_id,),
        )
        conn.execute(
            "UPDATE torrent_files SET state = 'evicted', "
            "local_path = NULL, updated_at = "
            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE torrent_id = ? AND storage_location <> '1FICHIER'",
            (torrent_id,),
        )
        conn.commit()


def _plan(candidate: Candidate, *, allow_private_delete: bool) -> tuple[str, str]:
    """Devuelve (acción_real, motivo)."""
    if candidate.privacy == "private" and candidate.action == "delete":
        if allow_private_delete:
            return "delete", "privado_borrado_explicito"
        return "pause", "privado_h_r"
    return candidate.action, "politica"


def abandon_idle(
    db_path: str,
    client: AbandonClient,
    *,
    limit: int = 20,
    grace_seconds: int = 120,
    min_age_seconds: int = 600,
    allow_private_delete: bool = False,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    report: dict[str, Any] = {
        "checked": 0,
        "planned": [],
        "kept": [],
        "paused": [],
        "deleted": [],
        "skipped_streaming": [],
        "skipped_recent": [],
        "skipped_no_media": [],
        "skipped_percent": 0,
        "skipped_disabled": 0,
        "skipped_already_paused": [],
        "errors": [],
        "dry_run": dry_run,
    }
    for candidate in _candidates(db_path, limit):
        report["checked"] += 1
        if candidate.under_percent <= 0:
            report["skipped_disabled"] += 1
            continue
        if candidate.progress >= candidate.under_percent / 100.0:
            report["skipped_percent"] += 1
            continue
        if not candidate.media_ids:
            report["skipped_no_media"].append(candidate.info_hash)
            continue
        if any(
            stream_active(
                db_path, media_id, grace_seconds=grace_seconds, now=current
            )
            for media_id in candidate.media_ids
        ):
            report["skipped_streaming"].extend(candidate.media_ids)
            continue
        age = _age_seconds(candidate, current)
        if age is not None and age < min_age_seconds:
            report["skipped_recent"].append(candidate.info_hash)
            continue
        action, reason = _plan(
            candidate, allow_private_delete=allow_private_delete
        )
        if action == "keep":
            report["kept"].append(candidate.info_hash)
            continue
        if candidate.state == "paused":
            report["skipped_already_paused"].append(candidate.info_hash)
            continue
        item = {
            "info_hash": candidate.info_hash,
            "torrent_id": candidate.torrent_id,
            "media_ids": list(candidate.media_ids),
            "action": action,
            "reason": reason,
            "progress": round(candidate.progress, 4),
            "tracker": candidate.tracker,
        }
        if dry_run:
            report["planned"].append(item)
            continue
        try:
            if action == "pause":
                client.pause(candidate.info_hash)
                _mark_paused(db_path, candidate.torrent_id)
                report["paused"].append(item)
            else:
                client.delete(candidate.info_hash, delete_files=True)
                _mark_deleted(db_path, candidate.torrent_id)
                report["deleted"].append(item)
        except Exception as exc:
            report["errors"].append(
                f"{candidate.info_hash[:12]}: {str(exc)[:200]}"
            )
    return report
