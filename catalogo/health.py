"""Reglas puras de publicación y salud.

No consulta Prowlarr ni qBittorrent. Solo decide, con los datos guardados,
si un activo se puede publicar como .strm.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .db import connect
from .policy import parse_time


@dataclass(frozen=True)
class PublishDecision:
    ok: bool
    reason: str


def publishable(
    *,
    source_kind: str,
    health: str | None = None,
    seeders: int | None = None,
    require_health: bool = False,
    min_seeders: int = 1,
) -> PublishDecision:
    if source_kind == "unavailable":
        return PublishDecision(False, "unavailable")
    if source_kind in ("library", "local_seed"):
        return PublishDecision(True, "local")
    if source_kind == "torrent":
        if require_health and (health or "unknown") != "alive":
            return PublishDecision(False, f"health_{health or 'unknown'}")
        if min_seeders > 0 and (seeders or 0) < min_seeders:
            return PublishDecision(False, "sin_peers")
        return PublishDecision(True, "torrent")
    if source_kind == "auto":
        return PublishDecision(True, "auto")
    return PublishDecision(False, f"source_{source_kind}")


def release_rank(
    *,
    audio: str | None,
    quality: str | None,
    seeders: int | None,
    size_bytes: int | None,
    max_size_bytes: int | None = None,
) -> tuple:
    """Orden para elegir candidato sin buscar en el play.

    Más alto es mejor. No sustituye a la salud: primero se filtra alive.
    """
    audio_rank = {
        "castellano": 3,
        "spanish_unverified": 2,
        "unknown": 1,
        "latino": 0,
        "vose": 0,
    }.get((audio or "unknown").lower(), 1)
    quality_rank = {
        "4k": 4,
        "1080p": 3,
        "720p": 2,
        "480p": 1,
    }.get((quality or "unknown").lower(), 0)
    fits = 0
    if max_size_bytes and size_bytes and size_bytes > max_size_bytes:
        fits = -1
    return (fits, audio_rank, quality_rank, -(size_bytes or 0), int(seeders or 0))


HEALTH_VALUES = {"unknown", "alive", "stale", "dead", "blocked"}


def classify_health(
    *,
    seeders: int | None,
    peers: int | None,
    checked_at: str | None,
    now: datetime | None = None,
    ttl_hours: int = 24,
    had_result: bool = True,
) -> str:
    """Clasifica una observación de Prowlarr/tracker.

    - sin observación -> unknown
    - observación vieja -> stale
    - con peers/seeders -> alive
    - sin peers -> dead
    - búsqueda sin resultados -> dead
    """
    if not had_result:
        return "dead"
    checked = parse_time(checked_at)
    if checked is None:
        return "unknown"
    current = now or datetime.now(timezone.utc)
    if (current - checked).total_seconds() > max(0, ttl_hours) * 3600:
        return "stale"
    if (seeders or 0) > 0 or (peers or 0) > 0:
        return "alive"
    return "dead"


def apply_health(
    db_path: str,
    release_id: int,
    *,
    seeders: int | None,
    peers: int | None,
    checked_at: str | None = None,
    health: str | None = None,
    live_title: str | None = None,
    live_tracker: str | None = None,
    live_guid: str | None = None,
    live_publish_date: str | None = None,
) -> dict[str, Any]:
    if health is not None and health not in HEALTH_VALUES:
        raise ValueError(f"health inválido: {health}")
    current = checked_at or datetime.now(timezone.utc).isoformat()
    status = health or classify_health(
        seeders=seeders, peers=peers, checked_at=current
    )
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE releases
            SET seeders = ?, peers = ?, health = ?, health_checked_at = ?,
                live_title = ?, live_tracker = ?, live_guid = ?,
                live_publish_date = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                seeders,
                peers,
                status,
                current,
                live_title,
                live_tracker,
                live_guid,
                live_publish_date,
                release_id,
            ),
        )
        row = conn.execute(
            "SELECT id, asset_id, tracker, seeders, peers, health, "
            "health_checked_at, live_title, live_tracker, live_guid "
            "FROM releases WHERE id = ?",
            (release_id,),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def summary(db_path: str) -> dict[str, Any]:
    with closing(connect(db_path)) as conn:
        by_health = {
            row["health"]: row["n"]
            for row in conn.execute(
                "SELECT health, COUNT(*) AS n FROM releases GROUP BY health"
            )
        }
        stale = conn.execute(
            "SELECT COUNT(*) FROM releases "
            "WHERE health IN ('unknown','stale')"
        ).fetchone()[0]
        alive = conn.execute(
            "SELECT COUNT(*) FROM releases WHERE health = 'alive'"
        ).fetchone()[0]
    return {"by_health": by_health, "alive": alive, "pending": stale}


def pending_releases(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, asset_id, tracker, title, seeders, peers, health,
                   health_checked_at
            FROM releases
            WHERE health IN ('unknown', 'stale')
            ORDER BY COALESCE(health_checked_at, ''), id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
