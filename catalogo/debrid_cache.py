"""Comprueba caché RD de releases públicos. Nunca toca privados."""

from __future__ import annotations

import hashlib
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .db import connect
from .debrid import DebridCache
from .torrent_meta import info_dict_bytes


def infohash_for_release(release: Any) -> str | None:
    value = (release["info_hash"] or "").lower().strip()
    if value:
        return value
    blob = release["torrent_blob"]
    if not blob:
        return None
    info = info_dict_bytes(bytes(blob))
    return hashlib.sha1(info).hexdigest() if info else None


def _candidates(db_path: str, limit: int) -> list[Any]:
    with closing(connect(db_path)) as conn:
        return conn.execute(
            """
            SELECT id, asset_id, tracker, title, size_bytes, info_hash,
                   torrent_blob, debrid_cached, privacy, source
            FROM releases
            WHERE privacy = 'public'
              AND source = 'jackett'
              AND (
                  debrid_checked_at IS NULL
                  OR (debrid_cached = 1 AND debrid_checked_at < datetime('now', '-1 day'))
                  OR (debrid_cached = 0 AND debrid_checked_at < datetime('now', '-7 days'))
              )
              AND (torrent_blob IS NOT NULL OR (info_hash IS NOT NULL AND info_hash <> ''))
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def cache_public(
    db_path: str,
    client: Any,
    *,
    limit: int = 10,
    delay: float = 1.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checked": 0,
        "cached": 0,
        "not_cached": 0,
        "skipped": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    rows = _candidates(db_path, limit)
    with closing(connect(db_path)) as conn:
        for index, row in enumerate(rows):
            if (row["privacy"] or "private") != "public":
                report["skipped"] += 1
                continue
            infohash = infohash_for_release(row)
            if not infohash:
                report["skipped"] += 1
                continue
            report["checked"] += 1
            try:
                result: DebridCache = client.check_torrent(infohash)
            except Exception as exc:
                report["errors"].append(f"{row['id']}: {exc}")
                continue
            if result.cached:
                report["cached"] += 1
            else:
                report["not_cached"] += 1
            if dry_run:
                continue
            conn.execute(
                """
                UPDATE releases SET
                    debrid_provider = 'realdebrid',
                    debrid_cached = ?,
                    debrid_checked_at = ?,
                    debrid_torrent_id = ?,
                    debrid_link = ?,
                    debrid_error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (
                    1 if result.cached else 0,
                    datetime.now(timezone.utc).isoformat(),
                    result.torrent_id,
                    result.link,
                    result.error,
                    int(row["id"]),
                ),
            )
            conn.commit()
            if delay and index < len(rows) - 1:
                time.sleep(delay)
    return report
