"""Repara torrents ondemand con error local.

Casos: apagón, ficheros borrados antes de completar, qBittorrent que arranca
con el torrent en error. Si el tracker sigue reconociendo el torrent, se
fuerza un recheck y se reanuda para que vuelva a descargar lo que falte.
"""

from __future__ import annotations

from contextlib import closing

from .broken import _is_broken
from .db import connect


class RepairRead:
    def trackers(self, info_hash: str) -> list[dict]: ...


class RepairWrite:
    def recheck(self, info_hash: str) -> None: ...
    def resume(self, info_hash: str) -> None: ...
    def set_force_start(self, info_hash: str, value: bool = True) -> None: ...


def repair_torrents(
    db_path,
    qbit_read: RepairRead,
    qbit_write: RepairWrite,
    *,
    limit: int = 5,
    dry_run: bool = True,
) -> dict:
    report: dict = {
        "checked": 0,
        "planned": [],
        "repaired": [],
        "skipped": [],
        "errors": [],
        "dry_run": dry_run,
    }
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT t.* FROM torrents t
            WHERE t.category = 'ondemand'
              AND t.state <> 'removed'
              AND (
                  t.state = 'error'
                  OR lower(COALESCE(t.qbit_state, '')) IN
                     ('error', 'missingfiles')
              )
            ORDER BY t.id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in rows:
            report["checked"] += 1
            info_hash = row["info_hash"]
            commitment = conn.execute(
                """
                SELECT 1 FROM seed_commitments
                WHERE (info_hash = ? OR
                       (? IS NOT NULL AND release_id = ?))
                  AND state IN ('approved', 'active', 'seeding')
                LIMIT 1
                """,
                (info_hash, row["release_id"], row["release_id"]),
            ).fetchone()
            if commitment is None:
                report["skipped"].append(
                    {"info_hash": info_hash, "reason": "sin_compromiso"}
                )
                continue
            try:
                trackers = qbit_read.trackers(info_hash)
            except Exception as exc:
                report["errors"].append(
                    f"{info_hash[:12]}: {str(exc)[:150]}"
                )
                continue
            if _is_broken(trackers):
                report["skipped"].append(
                    {"info_hash": info_hash, "reason": "tracker_roto"}
                )
                continue
            if dry_run:
                report["planned"].append({"info_hash": info_hash})
                continue
            try:
                qbit_write.recheck(info_hash)
                qbit_write.resume(info_hash)
                qbit_write.set_force_start(info_hash, True)
            except Exception as exc:
                report["errors"].append(
                    f"{info_hash[:12]}: {str(exc)[:150]}"
                )
                continue
            report["repaired"].append({"info_hash": info_hash})
    return report
