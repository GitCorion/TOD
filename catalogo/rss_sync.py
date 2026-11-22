"""Ingesta de novedades por RSS/latest de Prowlarr y Jackett.

No busca título a título: pide lo último de cada tracker, resuelve el tmdbId
y guarda títulos y releases. El fondo antiguo se sigue haciendo por lotes.
"""

from __future__ import annotations

import re
import time
from contextlib import closing
from datetime import datetime, timezone

from .bulk_sync import (
    _jackett_item,
    _upsert_release,
    parse_episode,
    resolve_indexers,
)
from .db import connect
from .importer import norm_title
from .public_sync import year_of
from .title_sync import upsert_episodes, upsert_movies
from .tmdb import episode_items, movie_item, tv_item
from .trackers import GENERAL_TRACKERS, spanish_trackers_from_indexers

EXT = re.compile(r"\.(mkv|mp4|avi|m4v|ts|m2ts)$", re.IGNORECASE)
CUT = re.compile(
    r"(?i)\b(2160p|1080p|720p|480p|web[- ]?dl|webrip|bluray|bdrip|"
    r"hdtv|x264|x265|hevc|avc)\b"
)
SEASON_MARK = re.compile(r"(?i)\bS\d{1,2}\b")


def _clean_title(value: str | None) -> str:
    text = EXT.sub("", value or "")
    text = re.sub(r"[._]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def guess_movie(value: str | None) -> tuple[str, int | None]:
    text = _clean_title(value)
    year = year_of(text)
    if year:
        parts = re.split(rf"(?<!\d){year}(?!\d)", text, maxsplit=1)
        if parts and len(parts[0].strip()) >= 3:
            return parts[0].strip(" -"), year
    return CUT.split(text, maxsplit=1)[0].strip(" -"), year


def guess_series(value: str | None) -> str:
    text = _clean_title(value)
    parts = re.split(
        r"(?i)\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{2,3}\b", text
    )
    return parts[0].strip(" -")


def _score(candidate: dict, wanted: str, year: int | None) -> float:
    name = norm_title(candidate.get("title"))
    target = norm_title(wanted)
    value = 0.0
    if name and target:
        if name == target:
            value += 10
        elif target in name or name in target:
            value += 5
    if year and candidate.get("year") == year:
        value += 25
    elif year and candidate.get("year"):
        value -= min(abs(int(candidate["year"]) - int(year)), 10)
    value += min(float(candidate.get("popularity") or 0), 2) / 1000
    return value


def _pick(candidates: list[dict], wanted: str, year: int | None) -> dict | None:
    if not candidates:
        return None
    best = max(candidates, key=lambda item: _score(item, wanted, year))
    threshold = 20 if year else 10
    return best if _score(best, wanted, year) >= threshold else None


def _is_series(result: dict) -> bool:
    title = result.get("title") or ""
    if parse_episode(title) is not None:
        return True
    if SEASON_MARK.search(title):
        return True
    category = str(result.get("category") or "")
    if category:
        return category.startswith("5") or "tv" in category.lower()
    return False


def _release_guid(tracker: str, result: dict) -> str:
    return f"prowlarr:{tracker}:{result.get('guid') or result.get('title') or ''}"


def publish_keys(
    db_path,
    library_root,
    keys: list[str],
    *,
    base_url: str,
    dry_run: bool = False,
    ctx=None,
) -> dict:
    """Exporta los .strm de novedades y cierra el seguimiento encontrado."""
    from contextlib import closing

    from .db import connect
    from .strm import export_strm
    from .strm_inventory import invalidate, inventory

    exported = 0
    if not dry_run:
        for index, key in enumerate(keys):
            if ctx is not None:
                ctx.check()
                if keys:
                    ctx.report(
                        f"exportando .strm ({index + 1}/{len(keys)})",
                        progress=0.5 + 0.5 * (index / max(1, len(keys))),
                    )
            export_strm(
                db_path,
                library_root,
                base_url=base_url,
                key=key,
                limit=None,
                skip_library=False,
            )
            exported += 1
    unmonitored = 0
    if exported:
        invalidate(library_root)
        published = inventory(library_root, ttl=0)["keys"]
        done = [key for key in keys if key in published]
        if done:
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE media_items SET monitored = 0 "
                    "WHERE key IN (" + ",".join("?" for _ in done) + ")",
                    done,
                )
                conn.commit()
            unmonitored = len(done)
    return {"exported": exported, "unmonitored": unmonitored}


def sync_rss(
    db_path,
    prowlarr,
    jackett,
    tmdb,
    *,
    limit: int = 200,
    delay: float = 0.3,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    checked_at = current.isoformat()
    report: dict = {
        "fetched": 0,
        "movies": 0,
        "series": 0,
        "new_titles": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": [],
        "errors": [],
        "keys": [],
        "dry_run": dry_run,
    }
    season_done: set[tuple[int, int]] = set()
    indexer_payload = prowlarr.indexers()
    found, _missing = resolve_indexers(
        prowlarr, GENERAL_TRACKERS, indexers=indexer_payload
    )
    spanish_trackers = spanish_trackers_from_indexers(indexer_payload)
    indexer_ids = [indexer_id for _, indexer_id in found]
    results: list[dict] = []
    for kind in ("movie", "series"):
        try:
            rows = prowlarr.search(
                "", media_type=kind, indexer_ids=indexer_ids, limit=limit
            )
        except Exception as exc:
            report["errors"].append(f"prowlarr {kind}: {str(exc)[:150]}")
            continue
        for row in rows:
            row = dict(row)
            row["_kind"] = kind
            results.append(row)
    if jackett is not None:
        try:
            rows = jackett.search(
                "all", "", media_type="movie", limit=limit
            )
        except Exception as exc:
            report["errors"].append(f"jackett all: {str(exc)[:150]}")
            rows = []
        for row in rows:
            tracker = row.get("indexer") or "Jackett"
            item = _jackett_item(row, tracker)
            item["publishDate"] = row.get("publish_date") or ""
            item["_kind"] = "series" if _is_series(item) else "movie"
            results.append(item)
    # Lo más reciente primero; los duplicados por guid se ignoran.
    results.sort(
        key=lambda row: str(
            row.get("publishDate") or row.get("publish_date") or ""
        ),
        reverse=True,
    )
    seen: set[tuple[str, str]] = set()
    movie_cache: dict[int, dict | None] = {}
    series_cache: dict[int, dict | None] = {}
    with closing(connect(db_path)) as conn:
        conn.isolation_level = None
        conn.commit()
        for result in results:
            if len(seen) >= limit:
                break
            tracker = str(result.get("indexer") or "")
            guid = _release_guid(tracker, result)
            key_seen = (tracker, guid)
            if key_seen in seen:
                continue
            seen.add(key_seen)
            report["fetched"] += 1
            try:
                seeders = int(result.get("seeders") or 0)
            except (TypeError, ValueError):
                seeders = 0
            if seeders < 1:
                report["skipped"].append(
                    {"title": result.get("title"), "reason": "sin_seeds"}
                )
                continue
            existing = conn.execute(
                "SELECT id FROM releases WHERE guid = ?", (guid,)
            ).fetchone()
            if existing is not None:
                if not dry_run:
                    conn.execute(
                        "UPDATE releases SET seeders=?, peers=?, "
                        "health='alive', health_checked_at=?, "
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                        "WHERE id=?",
                        (
                            seeders,
                            int(result.get("peers") or 0),
                            checked_at,
                            int(existing["id"]),
                        ),
                    )
                report["updated"] += 1
                continue
            kind = (
                "series"
                if _is_series(result)
                else (result.get("_kind") or "movie")
            )
            tmdb_id = 0
            try:
                tmdb_id = int(result.get("tmdbId") or 0)
            except (TypeError, ValueError):
                tmdb_id = 0
            if kind == "series":
                episode = parse_episode(result.get("title"))
                if episode is None:
                    report["skipped"].append(
                        {"title": result.get("title"), "reason": "pack"}
                    )
                    continue
                season, number = episode
                series = series_cache.get(tmdb_id)
                if tmdb_id and tmdb_id not in series_cache:
                    try:
                        series = tv_item(tmdb.tv_details(tmdb_id))
                    except Exception as exc:
                        report["errors"].append(
                            f"tmdb tv {tmdb_id}: {str(exc)[:120]}"
                        )
                        series = None
                    series_cache[tmdb_id] = series
                    if delay:
                        time.sleep(delay)
                if series is None:
                    wanted = guess_series(result.get("title"))
                    try:
                        payload = tmdb.search_tv(wanted)
                    except Exception as exc:
                        report["errors"].append(
                            f"tmdb tv buscar: {str(exc)[:120]}"
                        )
                        continue
                    candidates = [
                        item for item in (
                            tv_item(row) for row in payload.get("results") or []
                        ) if item
                    ]
                    series = _pick(candidates, wanted, None)
                if series is None:
                    report["skipped"].append(
                        {"title": result.get("title"), "reason": "sin_tmdb"}
                    )
                    continue
                if not dry_run:
                    upsert = upsert_episodes(
                        db_path,
                        series,
                        [{"season": season, "episode": number}],
                        source="rss",
                    )
                    # Ingesta automática: una serie que entra por el sistema
                    # debe traer toda la temporada emitida, no solo el
                    # episodio que apareció en el RSS.
                    marker = (int(series["tmdb"]), int(season))
                    if marker not in season_done:
                        season_done.add(marker)
                        try:
                            payload = tmdb.season_details(*marker)
                            episodes = episode_items(marker[1], payload)
                        except Exception as exc:
                            report["errors"].append(
                                f"tmdb temporada {marker}: {str(exc)[:120]}"
                            )
                            episodes = []
                        if episodes:
                            extra = upsert_episodes(
                                db_path, series, episodes, source="rss"
                            )
                            upsert["inserted"] += int(
                                extra.get("inserted") or 0
                            )
                            upsert["updated"] += int(
                                extra.get("updated") or 0
                            )
                        with closing(connect(db_path)) as mconn:
                            mconn.execute(
                                "UPDATE media_items SET monitored = 1, "
                                "monitored_at = COALESCE(monitored_at, ?), "
                                "monitor_source = COALESCE(monitor_source, 'rss') "
                                "WHERE kind = 'series' AND tmdb = ? "
                                "AND season = ? AND library_added = 0",
                                (checked_at, marker[0], marker[1]),
                            )
                            mconn.commit()
                else:
                    upsert = {"inserted": 1}
                if upsert.get("inserted"):
                    report["new_titles"] += 1
                media_key = (
                    f"series:tmdb:{series['tmdb']}:{season}:{number}"
                )
                if media_key not in report["keys"]:
                    report["keys"].append(media_key)
                report["series"] += 1
            else:
                movie = movie_cache.get(tmdb_id)
                if tmdb_id and tmdb_id not in movie_cache:
                    try:
                        movie = movie_item(tmdb.movie_details(tmdb_id))
                    except Exception as exc:
                        report["errors"].append(
                            f"tmdb movie {tmdb_id}: {str(exc)[:120]}"
                        )
                        movie = None
                    movie_cache[tmdb_id] = movie
                    if delay:
                        time.sleep(delay)
                if movie is None:
                    wanted, year = guess_movie(result.get("title"))
                    try:
                        payload = tmdb.search_movie(wanted, year=year)
                    except Exception as exc:
                        report["errors"].append(
                            f"tmdb movie buscar: {str(exc)[:120]}"
                        )
                        continue
                    candidates = [
                        item for item in (
                            movie_item(row)
                            for row in payload.get("results") or []
                        ) if item
                    ]
                    movie = _pick(candidates, wanted, year)
                if movie is None:
                    report["skipped"].append(
                        {"title": result.get("title"), "reason": "sin_tmdb"}
                    )
                    continue
                if not dry_run:
                    upsert = upsert_movies(db_path, [movie], source="rss")
                else:
                    upsert = {"inserted": 1}
                if upsert.get("inserted"):
                    report["new_titles"] += 1
                media_key = f"movie:tmdb:{movie['tmdb']}"
                if media_key not in report["keys"]:
                    report["keys"].append(media_key)
                report["movies"] += 1
            if dry_run:
                report["inserted"] += 1
                continue
            key_row = conn.execute(
                "SELECT id FROM media_items WHERE key = ?", (media_key,)
            ).fetchone()
            if key_row is None:
                report["skipped"].append(
                    {"title": result.get("title"), "reason": "sin_activo"}
                )
                continue
            outcome = _upsert_release(
                conn,
                int(key_row["id"]),
                tracker,
                result,
                checked_at,
                spanish_trackers=spanish_trackers,
            )
            report[outcome] += 1
        if not dry_run:
            conn.commit()
    return report
