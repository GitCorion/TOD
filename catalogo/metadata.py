"""Relleno de metadatos visuales (póster, sinopsis y nota) desde TMDB.

La información ya existe para buscar en trackers; esto solo mejora la
presentación del catálogo. Se ejecuta por lotes, nunca bloquea la UI y
respeta el rate-limit de TMDB.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone

from .db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pending_targets(
    db_path,
    *,
    limit: int = 0,
    missing_only: bool = True,
) -> list[dict]:
    """Títulos únicos (kind, tmdb) ordenados por popularidad."""
    query = """
        SELECT kind, tmdb, MAX(popularity) AS popularity,
               COUNT(*) AS rows
        FROM media_items
        WHERE tmdb IS NOT NULL AND tmdb > 0
    """
    if missing_only:
        query += " AND metadata_at IS NULL"
    query += " GROUP BY kind, tmdb ORDER BY popularity DESC, tmdb"
    params: list[int] = []
    if limit and limit > 0:
        query += " LIMIT ?"
        params.append(int(limit))
    with closing(connect(db_path)) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _fetch(client, kind: str, tmdb: int) -> dict:
    payload = (
        client.tv_details(tmdb)
        if kind == "series"
        else client.movie_details(tmdb)
    )
    if not isinstance(payload, dict):
        raise ValueError("respuesta vacía de TMDB")
    return {
        "kind": kind,
        "tmdb": tmdb,
        "poster_path": (payload.get("poster_path") or "").strip() or None,
        "backdrop_path": (payload.get("backdrop_path") or "").strip() or None,
        "overview": (payload.get("overview") or "").strip() or None,
        "vote_average": payload.get("vote_average"),
    }


def backfill(
    db_path,
    client,
    *,
    limit: int = 0,
    delay: float = 0.0,
    workers: int = 6,
    missing_only: bool = True,
    ctx=None,
) -> dict:
    """Actualiza los metadatos de cada (kind, tmdb) una sola vez.

    Las peticiones a TMDB van en paralelo; las escrituras se hacen en serie
    para no bloquear la base del panel.
    """
    targets = pending_targets(
        db_path, limit=limit, missing_only=missing_only
    )
    total = len(targets)
    updated = 0
    rows_updated = 0
    failed = 0
    errors: list[str] = []
    workers = max(1, min(int(workers or 1), 16))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch, client, target["kind"], int(target["tmdb"])): target
            for target in targets
        }
        for index, future in enumerate(as_completed(futures)):
            if ctx is not None:
                ctx.check()
                ctx.report(
                    f"metadatos {index + 1}/{total}",
                    progress=(index / max(1, total)),
                )
            try:
                item = future.result()
                with closing(connect(db_path)) as conn:
                    cursor = conn.execute(
                        """
                        UPDATE media_items SET
                            poster_path = COALESCE(?, poster_path),
                            backdrop_path = COALESCE(?, backdrop_path),
                            overview = COALESCE(?, overview),
                            vote_average = COALESCE(?, vote_average),
                            metadata_at = ?
                        WHERE kind = ? AND tmdb = ?
                        """,
                        (
                            item["poster_path"],
                            item["backdrop_path"],
                            item["overview"],
                            item["vote_average"],
                            _now(),
                            item["kind"],
                            item["tmdb"],
                        ),
                    )
                    conn.commit()
                    rows_updated += int(cursor.rowcount or 0)
                updated += 1
            except Exception as exc:  # TMDB puede fallar por un ID concreto
                failed += 1
                target = futures[future]
                if "404" in str(exc):
                    # Un ID que ya no existe en TMDB no se reintenta en cada pasada.
                    with closing(connect(db_path)) as conn:
                        conn.execute(
                            "UPDATE media_items SET metadata_at = ? "
                            "WHERE kind = ? AND tmdb = ?",
                            (_now(), target["kind"], int(target["tmdb"])),
                        )
                        conn.commit()
                if len(errors) < 20:
                    errors.append(
                        f"{target['kind']} {target['tmdb']}: {str(exc)[:160]}"
                    )
    return {
        "targets": total,
        "updated": updated,
        "rows": rows_updated,
        "failed": failed,
        "errors": errors,
    }
