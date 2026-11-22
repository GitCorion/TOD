"""Espejo de Radarr/Sonarr hacia el catálogo de TOD.

Radarr y Sonarr son la biblioteca a largo plazo; TOD es la capa de
reproducción instantánea. Este módulo copia sus altas a `media_items` para
que TOD pueda buscarlas, publicarlas como `.strm` y servirlas sin esperar a
la cola de los *arr.

No descarga ni modifica nada en Radarr/Sonarr.
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any

from .db import connect
from .title_sync import upsert_episodes, upsert_movies


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _image_url(images: Any, cover_type: str = "poster") -> str | None:
    """Devuelve el póster que ya conoce Radarr/Sonarr, sin llamar a TMDB."""
    for image in images or []:
        if not isinstance(image, dict):
            continue
        if (image.get("coverType") or "").lower() != cover_type:
            continue
        url = str(
            image.get("remoteUrl") or image.get("url") or ""
        ).strip()
        if url:
            return url
    return None


def _series_item(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        tmdb = int(row.get("tmdbId") or 0)
    except (TypeError, ValueError):
        tmdb = 0
    title = str(row.get("title") or "").strip()
    if not tmdb or not title:
        return None
    return {
        "tmdb": tmdb,
        "title": title,
        "year": row.get("year"),
        "original_title": row.get("originalTitle"),
        "overview": row.get("overview"),
        "poster_path": _image_url(row.get("images")),
        "backdrop_path": _image_url(row.get("images"), "fanart"),
        "vote_average": None,
        "popularity": None,
    }


def _movie_item(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        tmdb = int(row.get("tmdbId") or 0)
    except (TypeError, ValueError):
        tmdb = 0
    title = str(row.get("title") or "").strip()
    if not tmdb or not title:
        return None
    runtime = int(row.get("runtime") or 0)
    return {
        "tmdb": tmdb,
        "title": title,
        "year": row.get("year"),
        "release_date": _date(
            row.get("digitalRelease")
            or row.get("physicalRelease")
            or row.get("inCinemas")
        ),
        "original_title": row.get("originalTitle"),
        "overview": row.get("overview"),
        "poster_path": _image_url(row.get("images")),
        "backdrop_path": _image_url(row.get("images"), "fanart"),
        "vote_average": None,
        "popularity": row.get("popularity"),
        "runtime_seconds": runtime * 60 if runtime > 0 else None,
    }


def mirror_radarr(db_path, radarr, *, now: str | None = None) -> dict[str, Any]:
    stamp = now or _now()
    report: dict[str, Any] = {
        "seen": 0,
        "items": 0,
        "skipped": 0,
        "errors": [],
    }
    if radarr is None:
        return report
    try:
        rows = radarr.movies()
    except Exception as exc:
        report["errors"].append(f"radarr: {str(exc)[:180]}")
        return report

    items: list[dict[str, Any]] = []
    updates: list[tuple[int | None, str]] = []
    for row in rows:
        report["seen"] += 1
        item = _movie_item(row)
        if item is None:
            report["skipped"] += 1
            continue
        items.append(item)
        updates.append(
            (
                item.get("runtime_seconds"),
                f"movie:tmdb:{item['tmdb']}",
            )
        )
    if not items:
        return report

    result = upsert_movies(db_path, items, source="radarr")
    report.update(
        {
            "items": len(items),
            "inserted": result.get("inserted", 0),
            "updated": result.get("updated", 0),
        }
    )
    with closing(connect(db_path)) as conn:
        conn.executemany(
            """
            UPDATE media_items SET
                library_added = 1,
                library_added_at = COALESCE(library_added_at, ?),
                library_source = 'radarr',
                runtime_seconds = COALESCE(runtime_seconds, ?),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE key = ?
            """,
            [(stamp, runtime, key) for runtime, key in updates],
        )
        conn.commit()
    return report


def mirror_sonarr(db_path, sonarr, *, now: str | None = None) -> dict[str, Any]:
    stamp = now or _now()
    report: dict[str, Any] = {
        "seen": 0,
        "series": 0,
        "episodes": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    if sonarr is None:
        return report
    try:
        rows = sonarr.series()
    except Exception as exc:
        report["errors"].append(f"sonarr: {str(exc)[:180]}")
        return report

    all_states: list[tuple[int | None, str]] = []
    for series in rows:
        report["seen"] += 1
        item = _series_item(series)
        if item is None:
            report["skipped"] += 1
            continue
        try:
            episodes = sonarr.episodes(int(series.get("id") or 0))
        except Exception as exc:
            report["errors"].append(
                f"sonarr {item['title']}: {str(exc)[:150]}"
            )
            continue
        payload: list[dict[str, Any]] = []
        for episode in episodes:
            try:
                season = int(episode.get("seasonNumber") or 0)
                number = int(episode.get("episodeNumber") or 0)
            except (TypeError, ValueError):
                continue
            if season < 1 or number < 1:
                continue
            payload.append(
                {
                    "season": season,
                    "episode": number,
                    "name": episode.get("title"),
                    "air_date": _date(episode.get("airDate")),
                    "overview": episode.get("overview"),
                }
            )
            all_states.append(
                (
                    int(series.get("tvdbId") or 0) or None,
                    f"series:tmdb:{item['tmdb']}:{season}:{number}",
                )
            )
        if not payload:
            report["skipped"] += 1
            continue
        try:
            result = upsert_episodes(
                db_path, item, payload, source="sonarr"
            )
        except Exception as exc:
            report["errors"].append(
                f"sonarr {item['title']}: {str(exc)[:150]}"
            )
            continue
        report["series"] += 1
        report["episodes"] += len(payload)
        report["inserted"] += int(result.get("inserted") or 0)
        report["updated"] += int(result.get("updated") or 0)
        report["skipped"] += int(result.get("skipped") or 0)
        # Sonarr puede devolver cientos de series; no saturar su API.
        time.sleep(0.02)
    if all_states:
        with closing(connect(db_path)) as conn:
            conn.executemany(
                """
                UPDATE media_items SET
                    tvdb_id = COALESCE(?, tvdb_id),
                    library_added = 1,
                    library_added_at = COALESCE(library_added_at, ?),
                    library_source = 'sonarr',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE key = ?
                """,
                [(tvdb, stamp, key) for tvdb, key in all_states],
            )
            conn.commit()
    return report


def mirror_library(
    db_path, radarr=None, sonarr=None, *, now: str | None = None
) -> dict[str, Any]:
    return {
        "radarr": mirror_radarr(db_path, radarr, now=now),
        "sonarr": mirror_sonarr(db_path, sonarr, now=now),
    }


def pending_publish_keys(
    db_path,
    root,
    *,
    limit: int = 100,
) -> list[str]:
    """Títulos con fichero de biblioteca que aún no tienen `.strm`."""
    from . import strm_inventory

    published = strm_inventory.inventory(root)["keys"]
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.key
            FROM media_items m
            JOIN library_files lf
              ON lf.kind = m.kind AND lf.tmdb = m.tmdb
             AND (
                 m.kind = 'movie'
                 OR (lf.season = m.season AND lf.episode = m.episode)
             )
            WHERE m.library_added = 1
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [str(row["key"]) for row in rows if str(row["key"]) not in published]


def pending_source_keys(
    db_path,
    *,
    limit: int = 25,
    today: str | None = None,
) -> list[tuple[str, str]]:
    """Títulos espejados sin fuente viva y sin fichero local.

    Son los que hay que buscar en Prowlarr/Jackett para poder publicar su
    `.strm`. Se limita el lote para no bloquear la cola.
    """
    current = today or date.today().isoformat()
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT m.key, m.kind, m.tmdb
            FROM media_items m
            WHERE NOT EXISTS (
                  SELECT 1 FROM releases r
                  WHERE r.asset_id = m.id
                    AND r.health = 'alive'
                    AND COALESCE(r.seeders, 0) > 0
              )
              AND NOT EXISTS (
                  SELECT 1 FROM torrent_files f
                  WHERE f.asset_id = m.id
                    AND f.state IN ('ready', 'archived', 'seeding', 'complete')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM library_files lf
                  WHERE lf.kind = m.kind AND lf.tmdb = m.tmdb
                    AND (
                        m.kind = 'movie'
                        OR (lf.season = m.season AND lf.episode = m.episode)
                    )
              )
              AND (m.release_date IS NULL OR m.release_date <= ?)
              AND (m.air_date IS NULL OR m.air_date <= ?)
              AND (
                  m.monitor_last_search_at IS NULL
                  OR m.monitor_last_search_at <= datetime('now', '-1 day')
              )
            ORDER BY COALESCE(m.library_added_at, '') DESC,
                     COALESCE(m.popularity, 0) DESC, m.id DESC
            LIMIT ?
            """,
            (current, current, max(1, int(limit)) * 10),
        ).fetchall()
    result: list[tuple[str, str]] = []
    per_series: dict[int, int] = {}
    for row in rows:
        kind = str(row["kind"])
        tmdb = int(row["tmdb"] or 0)
        if kind == "series" and tmdb:
            # No gastar el lote entero en 20 episodios de la misma serie.
            if per_series.get(tmdb, 0) >= 2:
                continue
            per_series[tmdb] = per_series.get(tmdb, 0) + 1
        result.append((str(row["key"]), kind))
        if len(result) >= max(1, int(limit)):
            break
    return result
