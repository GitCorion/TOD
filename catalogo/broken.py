"""Elimina torrents que el tracker ya no reconoce y sus `.strm`.

Caso típico: el tracker sube un pack de temporada y los episodios sueltos
dejan de existir. qBittorrent recibe un error definitivo del tracker; aquí
se borra el torrent, se marca el release como muerto y se retiran los
`.strm` que apuntaban a él.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from .db import connect
from .strm import strm_path

BROKEN_MESSAGES = (
    "not registered",
    "unregistered",
    "torrent not found",
    "torrent does not exist",
    "has been deleted",
    "torrent has been deleted",
    "not authorized",
    "not authorised",
    "invalid passkey",
    "uploaded torrent not found",
)


class BrokenRead(Protocol):
    def trackers(self, info_hash: str) -> list[dict]: ...


class BrokenWrite(Protocol):
    def delete(self, info_hash: str, *, delete_files: bool = False) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_broken(trackers: list[dict] | None) -> bool:
    for tracker in trackers or []:
        message = (tracker.get("msg") or "").lower()
        if any(pattern in message for pattern in BROKEN_MESSAGES):
            return True
    return False


def _release_id_from_strm(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    params = parse_qs(urlparse(text).query)
    value = (params.get("release") or [""])[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _live_release(conn, asset_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM releases
        WHERE asset_id = ?
          AND health IN ('alive', 'unknown', 'stale')
          AND COALESCE(seeders, 0) >= 1
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone()
    return row is not None


def clean_broken(
    db_path,
    qbit_read: BrokenRead,
    qbit_write: BrokenWrite,
    *,
    library_root: str | Path = "/mnt/catalogo",
    limit: int = 10,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    root = Path(library_root)
    report: dict = {
        "checked": 0,
        "broken": [],
        "removed": [],
        "strm_removed": [],
        "errors": [],
        "dry_run": dry_run,
    }
    actions: dict[int, set[int]] = {}
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM torrents
            WHERE category = 'ondemand' AND state <> 'removed'
            ORDER BY id LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in rows:
            report["checked"] += 1
            try:
                trackers = qbit_read.trackers(row["info_hash"])
            except Exception as exc:
                report["errors"].append(
                    f"{row['info_hash'][:12]}: {str(exc)[:150]}"
                )
                continue
            if not _is_broken(trackers):
                continue
            release_ids: set[int] = set()
            asset_ids: set[int] = set()
            if row["release_id"] is not None:
                release_ids.add(int(row["release_id"]))
            files = conn.execute(
                "SELECT DISTINCT asset_id, release_id FROM torrent_files "
                "WHERE torrent_id = ?",
                (int(row["id"]),),
            ).fetchall()
            for file_row in files:
                if file_row["asset_id"] is not None:
                    asset_ids.add(int(file_row["asset_id"]))
                if file_row["release_id"] is not None:
                    release_ids.add(int(file_row["release_id"]))
            release = None
            if row["release_id"] is not None:
                release = conn.execute(
                    "SELECT * FROM releases WHERE id = ?",
                    (int(row["release_id"]),),
                ).fetchone()
            if release is not None and release["download_url"]:
                # Mismo pack: una fila por episodio, mismo enlace.
                siblings = conn.execute(
                    "SELECT id, asset_id FROM releases "
                    "WHERE tracker = ? AND download_url = ? AND title = ?",
                    (
                        release["tracker"],
                        release["download_url"],
                        release["title"],
                    ),
                ).fetchall()
                for sibling in siblings:
                    release_ids.add(int(sibling["id"]))
                    if sibling["asset_id"] is not None:
                        asset_ids.add(int(sibling["asset_id"]))
            report["broken"].append(
                {
                    "info_hash": row["info_hash"],
                    "release_ids": sorted(release_ids),
                    "asset_ids": sorted(asset_ids),
                }
            )
            if dry_run:
                continue
            try:
                qbit_write.delete(row["info_hash"], delete_files=True)
            except Exception as exc:
                report["errors"].append(
                    f"{row['info_hash'][:12]}: {str(exc)[:150]}"
                )
                continue
            for release_id in release_ids:
                conn.execute(
                    "UPDATE releases SET health = 'dead', health_checked_at = ? "
                    "WHERE id = ?",
                    (current.isoformat(), release_id),
                )
            conn.execute(
                "UPDATE torrents SET state = 'removed', qbit_state = 'removed', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (int(row["id"]),),
            )
            conn.execute(
                """
                UPDATE torrent_files SET state='evicted', local_path=NULL,
                    library_path=NULL,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE torrent_id = ?
                """,
                (int(row["id"]),),
            )
            if release_ids:
                placeholders = ",".join("?" for _ in release_ids)
                conn.execute(
                    "UPDATE seed_commitments SET state='cancelled', "
                    "reason='tracker_removed', completed_at=? "
                    "WHERE (info_hash = ? OR release_id IN ("
                    + placeholders + ")) "
                    "AND state IN ('approved','active','seeding')",
                    (current.isoformat(), row["info_hash"], *release_ids),
                )
            else:
                conn.execute(
                    "UPDATE seed_commitments SET state='cancelled', "
                    "reason='tracker_removed', completed_at=? "
                    "WHERE info_hash = ? "
                    "AND state IN ('approved','active','seeding')",
                    (current.isoformat(), row["info_hash"]),
                )
            for asset_id in asset_ids:
                actions.setdefault(asset_id, set()).update(release_ids)
            report["removed"].append(row["info_hash"])
        if not dry_run:
            conn.commit()
    if not dry_run:
        with closing(connect(db_path)) as conn:
            for asset_id, dead_ids in actions.items():
                asset = conn.execute(
                    "SELECT * FROM media_items WHERE id = ?", (asset_id,)
                ).fetchone()
                if asset is None:
                    continue
                data = dict(asset)
                default = strm_path(root, data)
                versions = list(
                    default.parent.glob(f"{default.stem} - *.strm")
                )
                for path in versions:
                    release_id = _release_id_from_strm(path)
                    if release_id is not None and release_id in dead_ids:
                        try:
                            path.unlink()
                            report["strm_removed"].append(str(path))
                        except FileNotFoundError:
                            pass
                if not _live_release(conn, asset_id) and default.exists():
                    try:
                        default.unlink()
                        report["strm_removed"].append(str(default))
                    except FileNotFoundError:
                        pass
                parent = default.parent
                stop = root
                while (
                    parent != stop
                    and parent.is_dir()
                    and not any(parent.iterdir())
                ):
                    if parent.parent == stop and parent.name in (
                        "movies",
                        "series",
                    ):
                        break
                    parent.rmdir()
                    parent = parent.parent
    return report
