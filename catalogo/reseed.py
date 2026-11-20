"""Re-descarga compromisos que perdieron el seed antes del mínimo.

qBittorrent manda para borrar, pero si su regla (o un fallo) elimina un
torrent antes de cumplir las horas del tracker, el catálogo lo vuelve a
descargar y a sembrar. Se apoya en `prepare_download` y en un play_intent
interno con origen `reseed`.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from . import play_intents
from .backend import DEFAULT_SAVE_PATH, prepare_download
from .db import connect


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reseed_pending(
    db_path,
    client,
    *,
    limit: int = 1,
    jackett_api_key: str | None = None,
    save_path: str = DEFAULT_SAVE_PATH,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    report: dict = {
        "checked": 0,
        "planned": [],
        "reseeded": [],
        "errors": [],
        "dry_run": dry_run,
    }
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT i.*, a.key
            FROM seed_commitments i
            JOIN media_items a ON a.id = i.asset_id
            WHERE i.state = 'approved'
              AND i.reason = 'reseed_early_removal'
              AND i.release_id IS NOT NULL
            ORDER BY i.id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    report["checked"] = len(rows)
    for row in rows:
        item = {
            "key": row["key"],
            "release_id": int(row["release_id"]),
            "tracker": row["tracker"],
        }
        if dry_run:
            report["planned"].append(item)
            continue
        play_intents.create(
            db_path,
            row["key"],
            source="manual",
            ttl_minutes=10,
            now=current,
        )
        try:
            handle = prepare_download(
                db_path,
                row["key"],
                client,
                release_id=int(row["release_id"]),
                jackett_api_key=jackett_api_key,
                save_path=save_path,
                now=current,
            )
        except Exception as exc:
            report["errors"].append(
                f"{row['key']}: {str(exc)[:200]}"
            )
            continue
        report["reseeded"].append(
            {**item, "info_hash": handle.get("info_hash")}
        )
    return report
