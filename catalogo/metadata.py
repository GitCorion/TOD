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
    raw_countries = (
        payload.get("origin_country")
        if kind == "series"
        else payload.get("production_countries")
    )
    codes = []
    for entry in raw_countries or []:
        code = (
            entry.get("iso_3166_1") if isinstance(entry, dict) else entry
        )
        code = str(code or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    genres = [
        str(g.get("name") or "").strip()
        for g in (payload.get("genres") or [])
        if isinstance(g, dict) and g.get("name")
    ]
    orig_lang = (payload.get("original_language") or "").strip() or None
    country_csv = ",".join(codes) or None
    is_anim_genre = any(
        "anim" in g.lower() or int(g.get("id") or 0) == 16
        for g in (payload.get("genres") or [])
        if isinstance(g, dict)
    )
    is_anime = 1 if (
        is_anim_genre and (orig_lang == "ja" or "JP" in codes)
    ) else 0
    runtime_sec = None
    if kind == "movie":
        rt = int(payload.get("runtime") or 0)
        runtime_sec = rt * 60 if rt > 0 else None
        companies = [
            str(c.get("name") or "").strip()
            for c in (payload.get("production_companies") or [])
            if isinstance(c, dict) and c.get("name")
        ]
        network_studio = companies[0] if companies else None
        series_status = (payload.get("status") or "").strip() or None
    else:
        rts = payload.get("episode_run_time") or []
        rt = int(rts[0]) if rts else 0
        if rt <= 0 and isinstance(payload.get("last_episode_to_air"), dict):
            rt = int(payload["last_episode_to_air"].get("runtime") or 0)
        runtime_sec = rt * 60 if rt > 0 else None
        nets = [
            str(n.get("name") or "").strip()
            for n in (payload.get("networks") or [])
            if isinstance(n, dict) and n.get("name")
        ]
        network_studio = nets[0] if nets else None
        series_status = (payload.get("status") or "").strip() or None
    imdb_id = (payload.get("imdb_id") or "").strip() or None
    return {
        "kind": kind,
        "tmdb": tmdb,
        "original_language": orig_lang,
        "original_country": country_csv,
        "poster_path": (payload.get("poster_path") or "").strip() or None,
        "backdrop_path": (payload.get("backdrop_path") or "").strip() or None,
        "overview": (payload.get("overview") or "").strip() or None,
        "vote_average": payload.get("vote_average"),
        "genres": ", ".join(genres) if genres else None,
        "series_status": series_status,
        "imdb_id": imdb_id,
        "network_studio": network_studio,
        "runtime_seconds": runtime_sec,
        "is_anime": is_anime,
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
                            original_language = COALESCE(?, original_language),
                            original_country = COALESCE(?, original_country),
                            genres = COALESCE(?, genres),
                            series_status = COALESCE(?, series_status),
                            imdb_id = COALESCE(?, imdb_id),
                            network_studio = COALESCE(?, network_studio),
                            runtime_seconds = COALESCE(?, runtime_seconds),
                            is_anime = CASE WHEN ? = 1 THEN 1 ELSE is_anime END,
                            series_type = CASE
                                WHEN ? = 1 AND kind = 'series' AND series_type = 'standard'
                                THEN 'anime' ELSE series_type END,
                            metadata_at = ?
                        WHERE kind = ? AND tmdb = ?
                        """,
                        (
                            item["poster_path"],
                            item["backdrop_path"],
                            item["overview"],
                            item["vote_average"],
                            item["original_language"],
                            item["original_country"],
                            item["genres"],
                            item["series_status"],
                            item["imdb_id"],
                            item["network_studio"],
                            item["runtime_seconds"],
                            item["is_anime"],
                            item["is_anime"],
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
