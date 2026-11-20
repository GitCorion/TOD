"""Mueve torrents privados completos a archivo solo si no hay stream activo."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .db import connect
from .stream_leases import active as stream_active

DEFAULT_TARGET = "/mnt/library/archive"
DEFAULT_SOURCE = "/mnt/descargas/ondemand"
DEFAULT_GRACE_SECONDS = 300


class MoveClient(Protocol):
    def set_location(self, info_hash: str, location: str) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _candidates(db_path: str, limit: int, source_root: str) -> list[Any]:
    with closing(connect(db_path)) as conn:
        return conn.execute(
            """
            SELECT t.id AS torrent_id, t.info_hash, t.save_path,
                   t.category, t.progress, t.qbit_state,
                   m.id AS media_item_id, m.media_id, r.tracker,
                   p.privacy
            FROM torrents t
            JOIN releases r ON r.id = t.release_id
            JOIN tracker_policies p ON p.tracker = r.tracker
            JOIN torrent_files f ON f.torrent_id = t.id
            JOIN media_items m ON m.id = f.asset_id
            WHERE t.category = 'ondemand'
              AND t.progress >= 1
              AND t.state IN ('seeding', 'complete')
              AND lower(COALESCE(t.qbit_state, '')) NOT IN ('moving')
              AND t.save_path LIKE ?
              AND p.privacy = 'private'
              AND p.enabled = 1
            GROUP BY t.id
            ORDER BY t.id
            LIMIT ?
            """,
            (source_root.rstrip("/") + "%", limit),
        ).fetchall()


def _files_for(conn, torrent_id: int) -> list[Any]:
    return conn.execute(
        "SELECT rel_path FROM torrent_files WHERE torrent_id = ?",
        (torrent_id,),
    ).fetchall()


def _media_ids_for(conn, torrent_id: int) -> list[str]:
    """Todos los episodios/películas enlazados a un torrent (packs)."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.media_id
        FROM torrent_files f
        JOIN media_items m ON m.id = f.asset_id
        WHERE f.torrent_id = ? AND m.media_id IS NOT NULL
        """,
        (torrent_id,),
    ).fetchall()
    return [str(row["media_id"]) for row in rows]


def move_completed(
    db_path: str,
    client: MoveClient,
    *,
    target: str = DEFAULT_TARGET,
    source_root: str = DEFAULT_SOURCE,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    limit: int = 10,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    report: dict[str, Any] = {
        "checked": 0,
        "planned": [],
        "moved": [],
        "skipped_streaming": [],
        "skipped_target_exists": [],
        "errors": [],
        "dry_run": dry_run,
    }
    for row in _candidates(db_path, limit, source_root):
        report["checked"] += 1
        torrent_id = int(row["torrent_id"])
        media_id = row["media_id"]
        with closing(connect(db_path)) as conn:
            media_ids = _media_ids_for(conn, torrent_id) or [media_id]
        # Un pack puede estar reproduciéndose por cualquier episodio, no
        # solo por el que quedó enlazado al torrent. Se comprueban todos.
        if any(
            stream_active(
                db_path, candidate, grace_seconds=grace_seconds, now=current
            )
            for candidate in media_ids
        ):
            report["skipped_streaming"].extend(media_ids)
            continue
        with closing(connect(db_path)) as conn:
            files = _files_for(conn, torrent_id)
        already = False
        for file_row in files:
            if file_row["rel_path"] and (
                Path(target) / file_row["rel_path"]
            ).is_file():
                already = True
                break
        if already:
            report["skipped_target_exists"].append(media_id)
            continue
        if dry_run:
            report["planned"].append(
                {
                    "media_id": media_id,
                    "info_hash": row["info_hash"],
                    "from": row["save_path"],
                    "to": target,
                }
            )
            continue
        try:
            client.set_location(row["info_hash"], target)
        except Exception as exc:
            report["errors"].append(f"{row['info_hash'][:12]}: {exc}")
            continue
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE media_items SET status = 'MOVING_TO_1FICHIER', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id IN ("
                "  SELECT asset_id FROM torrent_files "
                "  WHERE torrent_id = ? AND asset_id IS NOT NULL"
                ")",
                (torrent_id,),
            )
            conn.execute(
                "UPDATE torrents SET qbit_state = 'moving', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (int(row["torrent_id"]),),
            )
            conn.commit()
        report["moved"].append(
            {"media_id": media_id, "info_hash": row["info_hash"]}
        )
    return report
