"""Rellena la duración de películas desde TMDB.

El guardián de streaming calcula el bitrate necesario como
``tamaño / duración``. Jellyfin no siempre conoce la duración de un
``.strm``, así que se completa desde TMDB y se guarda en la base.
"""

from __future__ import annotations

import time
from contextlib import closing

from .db import connect


def sync_runtimes(
    db_path,
    client,
    *,
    limit: int = 200,
    delay: float = 0.25,
    only_missing: bool = True,
    kind: str = "movie",
) -> dict:
    report: dict = {
        "kind": kind,
        "checked": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    query = (
        "SELECT id, key, tmdb, title, year FROM media_items "
        "WHERE kind = ? AND tmdb IS NOT NULL"
    )
    params: list[object] = [kind]
    if only_missing:
        query += " AND (runtime_seconds IS NULL OR runtime_seconds <= 0)"
    query += " ORDER BY popularity DESC, id LIMIT ?"
    params.append(int(limit))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(query, params).fetchall()
    for index, row in enumerate(rows):
        report["checked"] += 1
        try:
            details = client.movie_details(int(row["tmdb"]))
            minutes = int(details.get("runtime") or 0)
        except Exception as exc:
            report["errors"].append(
                f"{row['key']}: {str(exc)[:150]}"
            )
            continue
        if minutes <= 0:
            report["skipped"] += 1
            continue
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE media_items SET runtime_seconds = ? WHERE id = ?",
                (minutes * 60, int(row["id"])),
            )
            conn.commit()
        report["updated"] += 1
        if delay and index < len(rows) - 1:
            time.sleep(delay)
    return report
