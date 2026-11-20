"""Normaliza el título de las series usando TMDB.

Los catálogos antiguos guardaban como título del activo el nombre del
episodio o del release. Jellyfin necesita una carpeta por serie, así que
todos los episodios de un mismo `tmdb` deben compartir título y año.
"""

from __future__ import annotations

import time
from contextlib import closing

from .db import connect
from .tmdb import tv_item


def normalize_series_titles(
    db_path,
    client,
    *,
    delay: float = 0.3,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT tmdb, COUNT(*) AS episodes, MIN(title) AS sample
            FROM media_items
            WHERE kind = 'series' AND tmdb IS NOT NULL
            GROUP BY tmdb
            ORDER BY episodes DESC, tmdb
            """
        ).fetchall()
    if limit:
        rows = rows[: int(limit)]
    report: dict = {
        "series_checked": 0,
        "series_updated": 0,
        "episodes_updated": 0,
        "already_ok": 0,
        "errors": [],
        "samples": [],
        "dry_run": dry_run,
    }
    for index, row in enumerate(rows):
        report["series_checked"] += 1
        try:
            details = client.tv_details(int(row["tmdb"]))
        except Exception as exc:
            report["errors"].append(f"tmdb {row['tmdb']}: {exc}")
            continue
        series = tv_item(details)
        if series is None or not series.get("title"):
            report["errors"].append(f"tmdb {row['tmdb']}: sin título")
            continue
        with closing(connect(db_path)) as conn:
            current = conn.execute(
                """
                SELECT COUNT(*) FROM media_items
                WHERE kind='series' AND tmdb = ?
                  AND title = ? AND (year = ? OR ? IS NULL)
                """,
                (
                    int(row["tmdb"]),
                    series["title"],
                    series.get("year"),
                    series.get("year"),
                ),
            ).fetchone()[0]
            if current == int(row["episodes"]):
                report["already_ok"] += 1
            else:
                report["series_updated"] += 1
                if len(report["samples"]) < 8:
                    report["samples"].append(
                        {
                            "tmdb": int(row["tmdb"]),
                            "old": row["sample"],
                            "new": series["title"],
                            "year": series.get("year"),
                            "episodes": int(row["episodes"]),
                        }
                    )
                if not dry_run:
                    cur = conn.execute(
                        """
                        UPDATE media_items SET
                            title = ?,
                            year = COALESCE(?, year),
                            original_title = COALESCE(?, original_title),
                            original_language = COALESCE(
                                ?, original_language
                            ),
                            popularity = COALESCE(?, popularity),
                            updated_at = strftime(
                                '%Y-%m-%dT%H:%M:%fZ', 'now'
                            )
                        WHERE kind = 'series' AND tmdb = ?
                        """,
                        (
                            series["title"],
                            series.get("year"),
                            series.get("original_title"),
                            series.get("original_language"),
                            series.get("popularity"),
                            int(row["tmdb"]),
                        ),
                    )
                    report["episodes_updated"] += cur.rowcount
                    conn.commit()
        if delay and index < len(rows) - 1:
            time.sleep(delay)
    return report
