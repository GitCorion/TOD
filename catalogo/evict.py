"""Borrado manual de la copia del catálogo para liberar espacio.

No forma parte del ciclo automático: la política es sembrar hasta que
1fichier borre el fichero. Este módulo solo se usa cuando se pide a mano y
el compromiso ya cumplió el mínimo de seed.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .db import connect
from .retention import evictable
from .stream_leases import active as stream_active

CATALOG_ROOTS = (
    "/mnt/descargas/ondemand",
    "/mnt/fichier/descargas",
    "/mnt/cache/tmp/ondemand",
)


class EvictClient(Protocol):
    def delete(self, info_hash: str, delete_files: bool = True) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _under_roots(path: str | None, roots: tuple[str, ...]) -> bool:
    if not path:
        return False
    candidate = Path(path)
    for root in roots:
        try:
            candidate.relative_to(Path(root))
            return True
        except ValueError:
            continue
    return False


def evict_completed(
    db_path,
    client: EvictClient,
    *,
    limit: int = 10,
    grace_seconds: int = 120,
    dry_run: bool = True,
    roots: tuple[str, ...] = CATALOG_ROOTS,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    intents = evictable(db_path, now=current)
    report: dict = {
        "checked": len(intents),
        "planned": [],
        "evicted": [],
        "closed": [],
        "skipped_streaming": [],
        "skipped_pack": [],
        "skipped_other_category": [],
        "errors": [],
        "dry_run": dry_run,
    }
    for item in intents[:limit]:
        intent_id = int(item["intent_id"])
        with closing(connect(db_path)) as conn:
            intent = conn.execute(
                """
                SELECT i.*, a.id AS asset_id, a.key, a.media_id
                FROM seed_commitments i
                JOIN media_items a ON a.id = i.asset_id
                WHERE i.id = ?
                """,
                (intent_id,),
            ).fetchone()
            if intent is None:
                continue
            torrent = None
            if intent["info_hash"]:
                torrent = conn.execute(
                    "SELECT * FROM torrents WHERE info_hash = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (intent["info_hash"],),
                ).fetchone()
            if torrent is None and intent["release_id"] is not None:
                torrent = conn.execute(
                    "SELECT * FROM torrents WHERE release_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (intent["release_id"],),
                ).fetchone()
            if torrent is None:
                conn.execute(
                    "UPDATE seed_commitments SET state='done', completed_at=? "
                    "WHERE id = ?",
                    (current.isoformat(), intent_id),
                )
                conn.execute(
                    "UPDATE media_items SET status='COLD', active_file_id=NULL "
                    "WHERE id = ?",
                    (int(intent["asset_id"]),),
                )
                conn.commit()
                report["closed"].append(intent["key"])
                continue
            if (torrent["category"] or "") != "ondemand":
                report["skipped_other_category"].append(intent["key"])
                continue
            shared = conn.execute(
                """
                SELECT COUNT(*) FROM seed_commitments i
                JOIN torrent_files f ON f.asset_id = i.asset_id
                WHERE f.torrent_id = ? AND i.id <> ?
                  AND i.state IN ('approved', 'active', 'seeding')
                """,
                (int(torrent["id"]), intent_id),
            ).fetchone()[0]
            if shared:
                # Un pack compartido sigue sembrando para otros episodios.
                report["skipped_pack"].append(intent["key"])
                continue
            media_key = intent["media_id"] or intent["key"]
            if stream_active(
                db_path, media_key, grace_seconds=grace_seconds, now=current
            ):
                report["skipped_streaming"].append(intent["key"])
                continue
            files = conn.execute(
                "SELECT local_path, library_path FROM torrent_files "
                "WHERE torrent_id = ?",
                (int(torrent["id"]),),
            ).fetchall()
            paths = [
                path
                for row in files
                for path in (row["local_path"], row["library_path"])
                if _under_roots(path, roots)
            ]
        if dry_run:
            report["planned"].append(
                {
                    "key": intent["key"],
                    "info_hash": torrent["info_hash"],
                    "paths": paths,
                }
            )
            continue
        try:
            client.delete(torrent["info_hash"], delete_files=True)
        except Exception as exc:
            report["errors"].append(
                f"{intent['key']}: {str(exc)[:200]}"
            )
            continue
        for path in paths:
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                report["errors"].append(f"{path}: {str(exc)[:120]}")
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE seed_commitments SET state='done', completed_at=? "
                "WHERE id = ?",
                (current.isoformat(), intent_id),
            )
            conn.execute(
                "UPDATE torrents SET state='removed', qbit_state='removed', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (int(torrent["id"]),),
            )
            conn.execute(
                """
                UPDATE torrent_files SET state='evicted', local_path=NULL,
                    library_path=NULL,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE torrent_id = ?
                """,
                (int(torrent["id"]),),
            )
            conn.execute(
                "UPDATE media_items SET status='COLD', active_file_id=NULL, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (int(intent["asset_id"]),),
            )
            conn.commit()
        report["evicted"].append(
            {
                "key": intent["key"],
                "info_hash": torrent["info_hash"],
                "paths": paths,
            }
        )
    return report
