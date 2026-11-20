"""Lote semanal de fondo antiguo.

Elige el año con más títulos pendientes dentro de una lista (de más reciente
a más antiguo), busca fuentes en los trackers y exporta el año completo.
"""

from __future__ import annotations

from contextlib import closing

from .db import connect
from .strm import export_strm
from .trackers import GENERAL_TRACKERS


def pending_query(trackers: tuple[str, ...]) -> tuple[str, list]:
    query = """
        SELECT COUNT(*) FROM media_items a
        WHERE a.kind = 'movie'
          AND a.year = ?
          AND a.status = 'COLD'
          AND NOT EXISTS (
              SELECT 1 FROM torrent_files f WHERE f.asset_id = a.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM library_files lf
              WHERE lf.kind = 'movie' AND lf.tmdb = a.tmdb
          )
    """
    if trackers:
        placeholders = ",".join("?" for _ in trackers)
        query += (
            " AND NOT EXISTS ("
            "  SELECT 1 FROM releases r"
            "  WHERE r.asset_id = a.id AND r.tracker IN ("
            f"    {placeholders}"
            "  )"
            ")"
        )
    else:
        # Sin lista de trackers, cualquier release cuenta como fuente.
        query += (
            " AND NOT EXISTS ("
            "  SELECT 1 FROM releases r WHERE r.asset_id = a.id"
            ")"
        )
    return query, list(trackers)


def pick_year(
    db_path, years: list[int], trackers: tuple[str, ...] = GENERAL_TRACKERS
) -> int | None:
    query, params = pending_query(trackers)
    with closing(connect(db_path)) as conn:
        for year in years:
            if conn.execute(query, [year] + params).fetchone()[0] > 0:
                return year
    return None


def run_backfill(
    db_path,
    prowlarr,
    jackett,
    *,
    years: list[int],
    limit: int = 250,
    delay: float = 8.0,
    out_dir=None,
    base_url: str = "http://127.0.0.1:8773",
    dry_run: bool = False,
    skip_export: bool = False,
) -> dict:
    from .bulk_sync import sync_sources

    year = pick_year(db_path, years)
    if year is None:
        return {
            "year": None,
            "reason": "sin_pendientes",
            "dry_run": dry_run,
        }
    report = sync_sources(
        db_path,
        prowlarr,
        jackett=jackett,
        year=year,
        limit=limit,
        delay=delay,
        kind="movie",
        dry_run=dry_run,
    )
    export = None
    if not dry_run and not skip_export and out_dir is not None:
        export = export_strm(
            db_path,
            out_dir,
            base_url=base_url,
            limit=None,
            year=year,
            kind="movie",
            skip_library=False,
        )
    return {"year": year, "sync": report, "export": export, "dry_run": dry_run}
