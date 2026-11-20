"""Operaciones de administración usadas por el panel web.

Todo pasa por las funciones del proyecto: no hay SQL de negocio ni comandos
de shell. Las tareas largas se ejecutan desde `jobs.JobRunner` y las acciones
puntuales se llaman directamente desde `web.py`.
"""

from __future__ import annotations

import time
import threading
from typing import Any

from .abandon import abandon_idle
from .arr import RadarrClient, SonarrClient
from .backfill import run_backfill
from .bridge import bridge_downloads
from .broken import clean_broken
from .bulk_sync import sync_sources
from .config import Config
from .debrid import DebridError, RealDebridClient
from .debrid_cache import cache_public
from .evict import evict_completed
from .health_worker import run as health_run
from .jackett import JackettClient, JackettError
from .jobs import JobContext, JobError
from .library_sync import sync_library
from .metadata import backfill as metadata_backfill
from .mover import move_completed
from .prune import prune_library
from .prowlarr import ProwlarrClient, ProwlarrError
from .qbit_limits import (
    DEFAULT_ONDEMAND_KBPS,
    DEFAULT_RADARR_KBPS,
    DEFAULT_SONARR_KBPS,
    apply_limits,
    limits_from_kbps,
)
from .qbit_sync import sync as qbit_sync
from .qbittorrent import QbittorrentClient, QbittorrentError
from .qbit_write import QbittorrentWriteClient
from .repair import repair_torrents
from .reseed import reseed_pending
from .rss_sync import publish_keys as publish_rss_keys, sync_rss
from .seeding import normalize_completed
from .strm import export_strm
from .title_sync import upsert_episodes, upsert_movies
from .tmdb import TmdbClient, TmdbError, episode_items, movie_item, tv_item, year_of_date
from .tracker_block import set_tracker_blocked
from .trackers import GENERAL_TRACKERS
from .worker import run_once

# ---------------------------------------------------------------------------
# Clientes


def _qb_read(config: Config) -> QbittorrentClient:
    try:
        return QbittorrentClient.from_config(config)
    except QbittorrentError as exc:
        raise JobError(str(exc)) from exc


def _qb_write(config: Config) -> QbittorrentWriteClient:
    try:
        return QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        raise JobError(str(exc)) from exc


def _prowlarr(config: Config) -> ProwlarrClient:
    try:
        return ProwlarrClient.from_config(config)
    except ProwlarrError as exc:
        raise JobError(str(exc)) from exc


def _jackett(config: Config) -> JackettClient | None:
    try:
        return JackettClient.from_config(config)
    except JackettError:
        return None


def _tmdb(config: Config) -> TmdbClient:
    try:
        return TmdbClient.from_config(config)
    except TmdbError as exc:
        raise JobError(str(exc)) from exc


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


# ---------------------------------------------------------------------------
# TMDB: búsqueda y alta


def _card(item: dict, raw: dict) -> dict:
    return {
        "kind": item.get("kind"),
        "tmdb": item.get("tmdb"),
        "title": item.get("title"),
        "original_title": item.get("original_title"),
        "year": item.get("year"),
        "overview": item.get("overview"),
        "original_language": item.get("original_language"),
        "popularity": item.get("popularity"),
        "poster_path": raw.get("poster_path"),
        "backdrop_path": raw.get("backdrop_path"),
        "already": False,
    }


def tmdb_search(
    config: Config,
    query: str,
    *,
    kind: str = "movie",
    year: int | None = None,
    page: int = 1,
) -> dict[str, Any]:
    client = _tmdb(config)
    query = (query or "").strip()
    if kind == "series":
        payload = (
            client.search_tv(query, year=year, page=page)
            if query
            else client.popular_tv(page=page)
        )
        items = [
            (tv_item(row), row) for row in payload.get("results") or []
        ]
    else:
        payload = (
            client.search_movie(query, year=year, page=page)
            if query
            else client.popular_movies(page=page)
        )
        items = [
            (movie_item(row), row) for row in payload.get("results") or []
        ]
    cards = [_card(item, raw) for item, raw in items if item]
    if cards:
        from contextlib import closing

        from . import ondemand, strm_inventory
        from .db import connect

        published_keys = strm_inventory.inventory(config.catalog_library)["keys"]
        downloaded_ids = ondemand.downloaded_asset_ids(config.db_path)
        with closing(connect(config.db_path)) as conn:
            for card in cards:
                rows = conn.execute(
                    "SELECT id, key, season, episode, library_added "
                    "FROM media_items WHERE kind = ? AND tmdb = ? "
                    "ORDER BY season, episode, id",
                    (card["kind"], card["tmdb"]),
                ).fetchall()
                card["already"] = bool(rows)
                card["asset_id"] = int(rows[0]["id"]) if rows else None
                card["catalog_episodes"] = len(rows)
                card["published_episodes"] = sum(
                    1 for row in rows if row["key"] in published_keys
                )
                card["library_added"] = sum(
                    1 for row in rows
                    if row["library_added"] or row["key"] in published_keys
                )
                card["downloaded_episodes"] = sum(
                    1 for row in rows if int(row["id"]) in downloaded_ids
                )
                card["published"] = card["published_episodes"] > 0
                card["downloaded"] = card["downloaded_episodes"] > 0
                if card["downloaded"]:
                    card["status"] = "downloaded"
                elif card["library_added"]:
                    card["status"] = "library"
                elif rows:
                    card["status"] = "catalog"
                else:
                    card["status"] = "none"
    return {
        "kind": kind,
        "query": query,
        "page": page,
        "total_pages": int(payload.get("total_pages") or 1),
        "results": cards,
    }


def tmdb_seasons(config: Config, tmdb_id: int) -> dict[str, Any]:
    client = _tmdb(config)
    payload = client.tv_details(int(tmdb_id))
    seasons = []
    for item in payload.get("seasons") or []:
        try:
            number = int(item.get("season_number") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        seasons.append(
            {
                "season": number,
                "name": item.get("name"),
                "episodes": int(item.get("episode_count") or 0),
                "air_date": item.get("air_date"),
                "poster_path": item.get("poster_path"),
            }
        )
    series = tv_item(payload) or {}
    return {"series": series, "seasons": seasons}


def tmdb_episodes(config: Config, tmdb_id: int, season: int) -> dict[str, Any]:
    client = _tmdb(config)
    payload = client.season_details(int(tmdb_id), int(season))
    episodes = episode_items(int(season), payload)
    return {
        "tmdb": int(tmdb_id),
        "season": int(season),
        "episodes": episodes,
    }


_SERIES_FULL_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_SERIES_FULL_LOCK = threading.Lock()


def tmdb_series_full(
    config: Config,
    tmdb_id: int,
    *,
    ttl: float = 3600.0,
) -> dict[str, Any]:
    """Serie completa de TMDB, con todas sus temporadas y episodios.

    Se cachea en memoria para no repetir una decena de peticiones cada vez
    que se abre la ficha. La base local se sigue consultando aparte.
    """
    tmdb_id = int(tmdb_id)
    cache_key = (str(config.db_path), tmdb_id)
    now = time.monotonic()
    with _SERIES_FULL_LOCK:
        cached = _SERIES_FULL_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]

    client = _tmdb(config)
    payload = client.tv_details(tmdb_id)
    series = tv_item(payload) or {}
    series.update(
        {
            "kind": "series",
            "tmdb": tmdb_id,
            "poster_path": (payload.get("poster_path") or "").strip() or None,
            "backdrop_path": (payload.get("backdrop_path") or "").strip() or None,
            "overview": (payload.get("overview") or "").strip() or None,
            "vote_average": payload.get("vote_average"),
        }
    )
    seasons: list[dict[str, Any]] = []
    errors: list[str] = []
    raw_seasons = payload.get("seasons") or []
    try:
        raw_seasons = sorted(
            raw_seasons,
            key=lambda item: int(item.get("season_number") or 0),
        )
    except (TypeError, ValueError):
        pass
    for entry in raw_seasons:
        try:
            number = int(entry.get("season_number") or 0)
        except (TypeError, ValueError):
            continue
        if number < 0:
            continue
        try:
            season_payload = client.season_details(tmdb_id, number)
            episodes = episode_items(number, season_payload)
        except Exception as exc:
            errors.append(f"temporada {number}: {str(exc)[:160]}")
            continue
        seasons.append(
            {
                "season": number,
                "name": entry.get("name") or (
                    "Especiales" if number == 0 else f"Temporada {number}"
                ),
                "air_date": entry.get("air_date"),
                "poster_path": entry.get("poster_path"),
                "episodes": episodes,
            }
        )
    data = {
        "series": series,
        "seasons": seasons,
        "errors": errors,
        "fetched_at": time.time(),
    }
    with _SERIES_FULL_LOCK:
        _SERIES_FULL_CACHE[cache_key] = (now, data)
    return data


def remove_from_library(config: Config, key: str) -> dict[str, Any]:
    """Elimina los .strm de un título, sin tocar la ficha ni las descargas."""
    from contextlib import closing
    from pathlib import Path as _Path

    from .db import connect
    from .strm import strm_path
    from .strm_inventory import invalidate

    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            "SELECT id, key, kind, title, year, season, episode "
            "FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
    if row is None:
        raise JobError("título no encontrado")
    item = dict(row)
    root = _Path(config.catalog_library)
    default = strm_path(root, item)
    paths = [default]
    if default.parent.is_dir():
        paths.extend(default.parent.glob(f"{default.stem} - *.strm"))
    removed = []
    for path in dict.fromkeys(paths):
        try:
            if path.is_file() and root.resolve() in path.resolve().parents:
                path.unlink()
                removed.append(str(path))
        except OSError:
            continue
    parent = default.parent
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    invalidate(root)
    with closing(connect(config.db_path)) as conn:
        conn.execute(
            "UPDATE media_items SET library_added = 0, library_added_at = NULL, "
            "monitored = 0, monitor_last_search_at = NULL WHERE id = ?",
            (int(item["id"]),),
        )
        conn.commit()
    return {"key": item["key"], "removed": len(removed), "paths": removed}


def remove_series_from_library(config: Config, tmdb: int) -> dict[str, Any]:
    """Quita de la biblioteca todos los .strm de una serie."""
    from contextlib import closing

    from .db import connect

    with closing(connect(config.db_path)) as conn:
        rows = conn.execute(
            "SELECT key FROM media_items WHERE kind='series' AND tmdb=?",
            (int(tmdb),),
        ).fetchall()
    removed = 0
    errors = []
    for row in rows:
        try:
            report = remove_from_library(config, row["key"])
            removed += int(report["removed"])
        except Exception as exc:
            errors.append(f"{row['key']}: {str(exc)[:120]}")
    return {"tmdb": int(tmdb), "episodes": len(rows), "removed": removed, "errors": errors}


def set_monitored(
    config: Config,
    key: str,
    *,
    monitored: bool = True,
    source: str = "manual",
) -> dict[str, Any]:
    from contextlib import closing

    from .db import connect

    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            "SELECT id, key FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
        if row is None:
            raise JobError("título no encontrado")
        conn.execute(
            "UPDATE media_items SET monitored = ?, monitored_at = ?, "
            "monitor_source = ? WHERE id = ?",
            (
                1 if monitored else 0,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if monitored else None,
                source if monitored else None,
                int(row["id"]),
            ),
        )
        conn.commit()
    return {"key": row["key"], "monitored": bool(monitored)}


def set_library(
    config: Config,
    *,
    key: str | None = None,
    series_tmdb: int | None = None,
    seasons: list[int] | None = None,
    member: bool = True,
    monitored: bool | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Marca pertenencia a biblioteca, sin exigir que exista un .strm."""
    from contextlib import closing

    from .db import connect

    where = ""
    params: list[Any] = []
    if key:
        where = "(key = ? OR media_id = ?)"
        params.extend([key, key])
    elif series_tmdb:
        where = "kind = 'series' AND tmdb = ?"
        params.append(int(series_tmdb))
        clean = sorted({int(value) for value in (seasons or [])})
        if clean:
            where += " AND season IN (" + ",".join("?" for _ in clean) + ")"
            params.extend(clean)
    else:
        raise JobError("falta key o serie")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with closing(connect(config.db_path)) as conn:
        rows = conn.execute(
            f"SELECT id, key FROM media_items WHERE {where}", params
        ).fetchall()
        if not rows:
            raise JobError("título no encontrado")
        fields = [
            "library_added = ?",
            "library_added_at = ?",
            "library_source = ?",
        ]
        values: list[Any] = [
            1 if member else 0,
            stamp if member else None,
            source if member else None,
        ]
        if monitored is not None:
            fields.append("monitored = ?")
            values.append(1 if monitored else 0)
        conn.execute(
            f"UPDATE media_items SET {', '.join(fields)} WHERE {where}",
            values + params,
        )
        conn.commit()
    return {
        "key": key,
        "tmdb": series_tmdb,
        "member": bool(member),
        "monitored": monitored,
        "titles": len(rows),
        "keys": [str(row["key"]) for row in rows],
    }


def monitor_titles(
    db_path,
    config: Config,
    *,
    keys: list[str] | None = None,
    limit: int = 50,
    delay: float = 2.0,
    force: bool = False,
    ctx=None,
) -> dict[str, Any]:
    """Busca fuentes para títulos en seguimiento y publica si aparecen."""
    from contextlib import closing

    from .db import connect
    from .strm_inventory import invalidate, inventory

    query = (
        "SELECT id, key, kind, title, season, episode, monitor_last_search_at "
        "FROM media_items WHERE monitored = 1 "
    )
    if not force:
        query += (
            "AND (kind <> 'movie' OR release_date IS NULL "
            "     OR date(release_date) <= date('now')) "
            "AND (kind <> 'series' OR air_date IS NULL "
            "     OR date(air_date) <= date('now')) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM torrent_files f "
            "  JOIN torrents t ON t.id = f.torrent_id "
            "  WHERE f.asset_id = media_items.id AND t.category = 'ondemand' "
            "    AND t.state IN ('waiting', 'downloading'))"
        )
    params: list[Any] = []
    selected_keys: list[str] = []
    if keys:
        selected_keys = [str(key) for key in keys if key][:500]
        if not selected_keys:
            return {"checked": 0, "found": 0, "waiting": 0, "errors": []}
        query += " AND key IN (" + ",".join("?" for _ in selected_keys) + ")"
        params.extend(selected_keys)
    query += " ORDER BY monitor_last_search_at IS NOT NULL, monitor_last_search_at, id LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with closing(connect(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        waiting_query = (
            "SELECT COUNT(*) FROM media_items WHERE monitored = 1 "
            "AND ((kind = 'movie' AND release_date IS NOT NULL "
            "      AND date(release_date) > date('now')) "
            "  OR (kind = 'series' AND air_date IS NOT NULL "
            "      AND date(air_date) > date('now')) "
            "  OR EXISTS ("
            "    SELECT 1 FROM torrent_files f "
            "    JOIN torrents t ON t.id = f.torrent_id "
            "    WHERE f.asset_id = media_items.id AND t.category = 'ondemand' "
            "      AND t.state IN ('waiting', 'downloading')))"
        )
        waiting_params: list[Any] = []
        if selected_keys:
            waiting_query += (
                " AND key IN (" + ",".join("?" for _ in selected_keys) + ")"
            )
            waiting_params.extend(selected_keys)
        waiting = int(
            conn.execute(waiting_query, waiting_params).fetchone()[0]
        )

    checked = 0
    found = 0
    errors: list[str] = []
    if ctx is None:
        from types import SimpleNamespace

        ctx = SimpleNamespace(
            config=config,
            report=lambda *args, **kwargs: None,
            check=lambda: None,
        )
    for index, row in enumerate(rows):
        if ctx is not None:
            ctx.check()
            ctx.report(
                f"seguimiento {index + 1}/{len(rows)}: {row['key']}",
                progress=(index / max(1, len(rows))),
            )
        checked += 1
        try:
            published = inventory(config.catalog_library)
            if row["key"] in published["keys"]:
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        "UPDATE media_items SET monitored = 0 WHERE id = ?",
                        (row["id"],),
                    )
                    conn.commit()
                continue
            _sync_keys(
                ctx,
                [row["key"]],
                kind=row["kind"],
                delay=0,
                min_seeders=1,
                no_jackett=False,
                recheck=True,
            )
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE media_items SET monitor_last_search_at = ? WHERE id = ?",
                    (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        row["id"],
                    ),
                )
                conn.commit()
            export_strm(
                db_path,
                config.catalog_library,
                base_url=config.playback_base_url,
                key=row["key"],
                limit=None,
                min_seeders=1,
                versions_filter="available",
                skip_library=False,
            )
            invalidate(config.catalog_library)
            published = inventory(config.catalog_library, ttl=0)
            if row["key"] in published["keys"]:
                found += 1
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        "UPDATE media_items SET monitored = 0 WHERE id = ?",
                        (row["id"],),
                    )
                    conn.commit()
        except Exception as exc:
            errors.append(f"{row['key']}: {str(exc)[:180]}")
        if delay and index < len(rows) - 1:
            time.sleep(max(0.0, delay))
    return {
        "checked": checked,
        "found": found,
        "waiting": waiting,
        "remaining": checked - found,
        "errors": errors[:20],
    }


def manual_search(
    config: Config,
    key: str,
    *,
    min_seeders: int = 0,
    no_jackett: bool = False,
    query: str | None = None,
) -> dict[str, Any]:
    """Busca releases ahora y devuelve las que se han guardado.

    No descarga nada: inserta candidatos y la UI decide cuál preparar.
    """
    from contextlib import closing

    from . import queries
    from .db import connect

    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            "SELECT id, key, kind, title FROM media_items "
            "WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
    if row is None:
        raise JobError("título no encontrado")
    asset_id = int(row["id"])
    custom = (query or "").strip()
    forced = bool(
        custom and custom.casefold() != (row["title"] or "").strip().casefold()
    )
    report = sync_sources(
        str(config.db_path),
        _prowlarr(config),
        jackett=None if no_jackett else _jackett(config),
        limit=1,
        delay=0,
        trackers=GENERAL_TRACKERS,
        min_seeders=max(0, int(min_seeders)),
        key=row["key"],
        kind=row["kind"],
        recheck=True,
        query_override=custom or None,
        accept_unmatched=forced,
    )
    with closing(connect(config.db_path)) as conn:
        releases = queries._release_rows(conn, asset_id)
    return {
        "asset_id": asset_id,
        "key": row["key"],
        "title": row["title"],
        "kind": row["kind"],
        "query": custom or None,
        "forced": forced,
        "summary": report,
        "releases": releases,
    }


_COLLECTION_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_MOVIE_COLLECTION_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any] | None]] = {}
_COLLECTION_LOCK = threading.Lock()


def tmdb_collection(
    config: Config,
    collection_id: int,
    *,
    ttl: float = 3600.0,
) -> dict[str, Any]:
    """Colección de TMDB normalizada, cacheada en memoria."""
    collection_id = int(collection_id)
    cache_key = (str(config.db_path), collection_id)
    now = time.monotonic()
    with _COLLECTION_LOCK:
        cached = _COLLECTION_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
    payload = _tmdb(config).collection_details(collection_id)
    parts = []
    for part in payload.get("parts") or []:
        try:
            tmdb = int(part.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not tmdb:
            continue
        parts.append(
            {
                "tmdb": tmdb,
                "title": (part.get("title") or part.get("original_title") or "").strip() or None,
                "year": year_of_date(part.get("release_date")),
                "release_date": part.get("release_date"),
                "poster_path": part.get("poster_path"),
                "backdrop_path": part.get("backdrop_path"),
                "overview": (part.get("overview") or "").strip() or None,
                "popularity": part.get("popularity"),
            }
        )
    data = {
        "id": collection_id,
        "name": payload.get("name") or f"Colección {collection_id}",
        "overview": (payload.get("overview") or "").strip() or None,
        "poster_path": payload.get("poster_path"),
        "backdrop_path": payload.get("backdrop_path"),
        "parts": parts,
    }
    with _COLLECTION_LOCK:
        _COLLECTION_CACHE[cache_key] = (now, data)
    return data


def tmdb_movie_collection(config: Config, tmdb_id: int) -> dict[str, Any] | None:
    """Colección a la que pertenece una película, si existe."""
    tmdb_id = int(tmdb_id)
    cache_key = (str(config.db_path), tmdb_id)
    now = time.monotonic()
    with _COLLECTION_LOCK:
        cached = _MOVIE_COLLECTION_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < 3600:
            return cached[1]
    movie = _tmdb(config).movie_details(tmdb_id)
    belongs = movie.get("belongs_to_collection")
    if not belongs:
        with _COLLECTION_LOCK:
            _MOVIE_COLLECTION_CACHE[cache_key] = (now, None)
        return None
    collection = tmdb_collection(config, int(belongs.get("id")))
    summary = {
        "id": collection["id"],
        "name": collection["name"],
        "poster_path": collection["poster_path"],
        "backdrop_path": collection["backdrop_path"],
        "parts_count": len(collection["parts"]),
    }
    with _COLLECTION_LOCK:
        _MOVIE_COLLECTION_CACHE[cache_key] = (now, summary)
    return summary


def import_collection(config: Config, collection_id: int) -> dict[str, Any]:
    """Da de alta las películas que faltan de una colección."""
    collection = tmdb_collection(config, collection_id)
    if not collection["parts"]:
        raise JobError("la colección no tiene películas")
    keys = []
    imported = 0
    existing = 0
    errors = []
    from contextlib import closing

    from .db import connect

    with closing(connect(config.db_path)) as conn:
        known = {
            row["key"]
            for row in conn.execute(
                "SELECT key FROM media_items WHERE kind='movie'"
            ).fetchall()
        }
    for part in collection["parts"]:
        key = f"movie:tmdb:{part['tmdb']}"
        keys.append(key)
        if key in known:
            existing += 1
            continue
        try:
            add_movie(config, int(part["tmdb"]))
            imported += 1
        except Exception as exc:
            errors.append(f"{part['tmdb']}: {str(exc)[:160]}")
    return {
        "collection": collection,
        "keys": keys,
        "imported": imported,
        "existing": existing,
        "errors": errors,
    }


def tmdb_poster(config: Config, kind: str, tmdb_id: int) -> str | None:
    try:
        client = _tmdb(config)
        payload = (
            client.tv_details(int(tmdb_id))
            if kind == "series"
            else client.movie_details(int(tmdb_id))
        )
        return payload.get("poster_path")
    except Exception:
        return None


def add_movie(config: Config, tmdb_id: int) -> dict[str, Any]:
    client = _tmdb(config)
    raw = client.movie_details(int(tmdb_id))
    item = movie_item(raw)
    if item is None:
        raise JobError("TMDB no devolvió una película válida")
    try:
        item["aliases"] = client.alternative_titles("movie", int(tmdb_id))
    except Exception:
        pass
    report = upsert_movies(config.db_path, [item], source="tmdb")
    key = f"movie:tmdb:{int(tmdb_id)}"
    asset_id = None
    from contextlib import closing

    from .db import connect

    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            "SELECT id FROM media_items WHERE key = ?", (key,)
        ).fetchone()
        if row is not None:
            asset_id = int(row["id"])
    return {
        "kind": "movie",
        "key": key,
        "asset_id": asset_id,
        "item": item,
        "poster_path": raw.get("poster_path"),
        **report,
    }


def add_series(
    config: Config,
    tmdb_id: int,
    seasons: list[int],
) -> dict[str, Any]:
    client = _tmdb(config)
    raw = client.tv_details(int(tmdb_id))
    series = tv_item(raw)
    if series is None:
        raise JobError("TMDB no devolvió una serie válida")
    try:
        series["aliases"] = client.alternative_titles("series", int(tmdb_id))
    except Exception:
        pass
    episode_keys: list[str] = []
    episodes_total = 0
    reports = []
    for season in sorted({int(value) for value in seasons if int(value) > 0}):
        payload = client.season_details(int(tmdb_id), season)
        episodes = episode_items(season, payload)
        if episodes:
            reports.append(upsert_episodes(config.db_path, series, episodes))
            episodes_total += len(episodes)
            episode_keys.extend(
                f"series:tmdb:{int(tmdb_id)}:{season}:{int(item['episode'])}"
                for item in episodes
            )
        time.sleep(0.25)
    return {
        "kind": "series",
        "tmdb": int(tmdb_id),
        "title": series.get("title"),
        "seasons": seasons,
        "episodes": episodes_total,
        "keys": episode_keys,
        "reports": reports,
    }


# ---------------------------------------------------------------------------
# Acciones puntuales


def block_tracker(
    config: Config,
    tracker: str,
    *,
    blocked: bool,
    reason: str = "downloads_broken",
    dry_run: bool = False,
) -> dict[str, Any]:
    if not (tracker or "").strip():
        raise JobError("falta el tracker")
    return set_tracker_blocked(
        str(config.db_path),
        tracker.strip(),
        blocked=bool(blocked),
        reason=reason or "downloads_broken",
        dry_run=dry_run,
    )


def set_target(
    config: Config,
    key: str,
    source_kind: str,
    *,
    release_id: int | None = None,
    file_id: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    from .resolver import Resolver

    return Resolver(config.db_path).set_target(
        key,
        source_kind,
        release_id=release_id,
        file_id=file_id,
        note=note,
    )


def prepare_release(
    config: Config,
    play_service: Any,
    release_id: int,
    *,
    client_ip: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Crea intención y descarga un release concreto bajo admisión."""
    from contextlib import closing

    from . import play_intents
    from .db import connect

    if play_service is None:
        raise JobError(
            "reproducción no configurada: falta QBIT_PASS o el servicio no arrancó"
        )
    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            """
            SELECT r.id, r.health, r.privacy, m.key, m.title
            FROM releases r JOIN media_items m ON m.id = r.asset_id
            WHERE r.id = ?
            """,
            (int(release_id),),
        ).fetchone()
    if row is None:
        raise JobError("release no encontrado")
    if (row["health"] or "unknown") in ("blocked", "dead"):
        raise JobError(
            f"el release está {row['health']}: busca otra versión o verifica su salud"
        )
    key = row["key"]
    play_intents.create(
        str(config.db_path),
        key,
        source="manual",
        client_ip=client_ip,
        user_agent="catalogo-web",
    )
    handle = play_service.ensure(key, release_id=int(release_id), force=force)
    return {
        "key": key,
        "title": row["title"],
        "release_id": int(release_id),
        "info_hash": handle.get("info_hash"),
        "handle": handle,
    }


def download_now(
    config: Config,
    play_service: Any,
    key: str,
    *,
    release_id: int | None = None,
    client_ip: str | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Descarga manual forzada: salta cola global y límite on-demand."""
    from contextlib import closing

    from . import play_intents
    from .db import connect

    if play_service is None:
        raise JobError(
            "reproducción no configurada: falta QBIT_PASS o el servicio no arrancó"
        )
    with closing(connect(config.db_path)) as conn:
        row = conn.execute(
            "SELECT id, key, title FROM media_items "
            "WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
    if row is None:
        raise JobError("título no encontrado")
    key = row["key"]
    play_intents.create(
        str(config.db_path),
        key,
        source="manual",
        client_ip=client_ip,
        user_agent="catalogo-web-download",
    )
    handle = play_service.ensure(
        key, release_id=release_id, force=force
    )
    return {
        "key": key,
        "title": row["title"],
        "release_id": handle.get("release_id"),
        "info_hash": handle.get("info_hash"),
        "forced": bool(force),
        "handle": handle,
    }


def qbit_action(
    config: Config,
    info_hash: str,
    action: str,
    *,
    delete_files: bool = False,
) -> dict[str, Any]:
    from contextlib import closing

    from .db import connect

    info_hash = (info_hash or "").strip().lower()
    if not info_hash:
        raise JobError("falta el hash")
    action = (action or "").strip().lower()
    if action not in ("pause", "resume", "recheck", "delete"):
        raise JobError("acción no válida")
    if action == "delete" and delete_files:
        with closing(connect(config.db_path)) as conn:
            row = conn.execute(
                "SELECT privacy FROM releases WHERE info_hash = ? LIMIT 1",
                (info_hash,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT r.privacy FROM torrents t "
                    "JOIN releases r ON r.id = t.release_id "
                    "WHERE t.info_hash = ? LIMIT 1",
                    (info_hash,),
                ).fetchone()
        if row is not None and (row["privacy"] or "private") == "private":
            raise JobError(
                "no se borran ficheros de un tracker privado desde el panel; "
                "pausa el torrent o usa la CLI con autorización explícita"
            )
    client = _qb_write(config)
    if action == "pause":
        client.pause(info_hash)
    elif action == "resume":
        client.resume(info_hash)
    elif action == "recheck":
        client.recheck(info_hash)
    else:
        client.delete(info_hash, delete_files=bool(delete_files))
    return {"info_hash": info_hash, "action": action, "delete_files": bool(delete_files)}


def apply_qbit_limits(
    config: Config,
    *,
    radarr_kbps: int = DEFAULT_RADARR_KBPS,
    sonarr_kbps: int = DEFAULT_SONARR_KBPS,
    ondemand_kbps: int = DEFAULT_ONDEMAND_KBPS,
    dry_run: bool = False,
) -> dict[str, Any]:
    client = _qb_write(config)
    return apply_limits(
        client,
        limits_from_kbps(radarr_kbps, sonarr_kbps, ondemand_kbps),
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Tareas


def _sync_keys(
    ctx: JobContext,
    keys: list[str],
    *,
    kind: str,
    delay: float,
    min_seeders: int,
    no_jackett: bool,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
    recheck: bool = False,
) -> dict[str, Any]:
    client = _prowlarr(ctx.config)
    jackett = None if no_jackett else _jackett(ctx.config)
    summary = {
        "keys": len(keys),
        "done": 0,
        "assets_checked": 0,
        "results": 0,
        "accepted": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "per_key": [],
    }
    for index, key in enumerate(keys):
        ctx.check()
        ctx.report(
            f"buscando fuentes para {key} ({index + 1}/{len(keys)})",
            progress=progress_base + progress_span * (index / max(1, len(keys))),
        )
        try:
            report = sync_sources(
                str(ctx.config.db_path),
                client,
                jackett=jackett,
                limit=1,
                delay=0,
                trackers=GENERAL_TRACKERS,
                min_seeders=min_seeders,
                key=key,
                kind=kind,
                recheck=recheck,
            )
        except Exception as exc:
            summary["errors"].append(f"{key}: {str(exc)[:250]}")
            continue
        summary["done"] += 1
        for field in (
            "assets_checked",
            "results",
            "accepted",
            "inserted",
            "updated",
            "skipped",
        ):
            summary[field] += int(report.get(field) or 0)
        summary["errors"].extend(report.get("errors") or [])
        summary["per_key"].append(
            {
                "key": key,
                "assets_checked": report.get("assets_checked", 0),
                "results": report.get("results", 0),
                "accepted": report.get("accepted", 0),
                "inserted": report.get("inserted", 0),
            }
        )
        if delay and index < len(keys) - 1:
            time.sleep(max(0.0, delay))
    return summary


def job_sync_sources(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    keys = [str(value) for value in params.get("keys") or [] if value]
    kind = "series" if params.get("kind") == "series" else "movie"
    delay = _float(params.get("delay"), 0.5)
    min_seeders = _int(params.get("min_seeders"), 1)
    no_jackett = _bool(params.get("no_jackett"), False)
    if keys:
        return _sync_keys(
            ctx,
            keys,
            kind=kind,
            delay=delay,
            min_seeders=min_seeders,
            no_jackett=no_jackett,
        )
    year = _int(params.get("year"), 0) or None
    limit = _int(params.get("limit"), 10)
    ctx.report(f"buscando fuentes ({kind}, {limit} títulos)", progress=0.1)
    report = sync_sources(
        str(ctx.config.db_path),
        _prowlarr(ctx.config),
        jackett=None if no_jackett else _jackett(ctx.config),
        year=year,
        limit=limit,
        delay=max(0.0, _float(params.get("delay"), 8.0)),
        trackers=GENERAL_TRACKERS,
        min_seeders=min_seeders,
        kind=kind,
    )
    ctx.report("fuentes sincronizadas", progress=0.95)
    return report


def job_health(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    ctx.report("consultando Prowlarr", progress=0.05)
    report = health_run(
        str(ctx.config.db_path),
        _prowlarr(ctx.config),
        limit=_int(params.get("limit"), 25),
        delay=max(0.0, _float(params.get("delay"), 1.0)),
        dry_run=_bool(params.get("dry_run"), False),
        tracker=params.get("tracker") or None,
        key=params.get("key") or None,
        ttl_hours=_int(params.get("ttl_hours"), 24),
    )
    ctx.report("salud actualizada", progress=0.95)
    return report


def job_rss(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    ctx.report("leyendo novedades de Prowlarr/Jackett", progress=0.05)
    report = sync_rss(
        str(ctx.config.db_path),
        _prowlarr(ctx.config),
        _jackett(ctx.config),
        _tmdb(ctx.config),
        limit=_int(params.get("limit"), 200),
        delay=max(0.0, _float(params.get("delay"), 0.3)),
        dry_run=_bool(params.get("dry_run"), False),
    )
    keys = report.pop("keys", [])
    publish = publish_rss_keys(
        ctx.config.db_path,
        ctx.config.catalog_library,
        keys,
        base_url=params.get("base_url") or ctx.config.playback_base_url,
        dry_run=_bool(params.get("dry_run"), False)
        or not _bool(params.get("export"), True),
        ctx=ctx,
    )
    report["exported_keys"] = publish["exported"]
    report["unmonitored_keys"] = publish["unmonitored"]
    return report


def job_backfill(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    years = [
        int(value)
        for value in params.get("years") or [2026, 2025, 2024, 2023, 2022]
    ]
    ctx.report("eligiendo año y buscando lote", progress=0.05)
    report = run_backfill(
        str(ctx.config.db_path),
        _prowlarr(ctx.config),
        _jackett(ctx.config),
        years=years,
        limit=_int(params.get("limit"), 250),
        delay=max(0.0, _float(params.get("delay"), 8.0)),
        out_dir=ctx.config.catalog_library,
        base_url=params.get("base_url") or ctx.config.playback_base_url,
        dry_run=_bool(params.get("dry_run"), False),
        skip_export=not _bool(params.get("export"), True),
    )
    ctx.report("lote terminado", progress=0.95)
    return report


def job_export(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    keys = [str(value) for value in params.get("keys") or [] if value]
    base_url = params.get("base_url") or ctx.config.playback_base_url
    min_seeders = _int(params.get("min_seeders"), 1)
    versions = params.get("versions") or "available"
    if versions not in ("available", "alive", "all"):
        versions = "available"
    if keys:
        written = 0
        unchanged = 0
        skipped = 0
        reports = []
        for index, key in enumerate(keys):
            ctx.check()
            ctx.report(
                f"exportando {key} ({index + 1}/{len(keys)})",
                progress=index / max(1, len(keys)),
            )
            report = export_strm(
                ctx.config.db_path,
                ctx.config.catalog_library,
                base_url=base_url,
                key=key,
                limit=None,
                min_seeders=min_seeders,
                versions_filter=versions,
                include_unavailable=_bool(params.get("include_unavailable"), False),
                require_health=_bool(params.get("require_health"), False),
                skip_library=_bool(params.get("skip_library"), False),
            )
            written += int(report.get("written") or 0)
            unchanged += int(report.get("unchanged") or 0)
            skipped += int(report.get("skipped") or 0)
            reports.append({"key": key, **{
                k: report.get(k) for k in ("written", "unchanged", "skipped")
            }})
        from .strm_inventory import invalidate

        invalidate(ctx.config.catalog_library)
        return {
            "keys": len(keys),
            "written": written,
            "unchanged": unchanged,
            "skipped": skipped,
            "reports": reports,
        }
    ctx.report("exportando catálogo", progress=0.1)
    report = export_strm(
        ctx.config.db_path,
        ctx.config.catalog_library,
        base_url=base_url,
        limit=_int(params.get("limit"), 0) or None,
        year=_int(params.get("year"), 0) or None,
        kind=params.get("kind") if params.get("kind") in ("movie", "series") else None,
        min_seeders=min_seeders,
        versions_filter=versions,
        include_unavailable=_bool(params.get("include_unavailable"), False),
        require_health=_bool(params.get("require_health"), False),
        skip_library=_bool(params.get("skip_library"), False),
        prune=_bool(params.get("prune"), False),
    )
    ctx.report("exportación terminada", progress=0.95)
    from .strm_inventory import invalidate

    invalidate(ctx.config.catalog_library)
    return report


def job_qbit_sync(ctx: JobContext) -> dict[str, Any]:
    ctx.report("sincronizando qBittorrent", progress=0.1)
    report = qbit_sync(
        str(ctx.config.db_path),
        _qb_read(ctx.config),
        radarr_db=str(ctx.config.radarr_db),
        sonarr_db=str(ctx.config.sonarr_db),
        dry_run=_bool(ctx.params.get("dry_run"), False),
        check_exists=_bool(ctx.params.get("check_exists"), True),
    )
    ctx.report("qBittorrent sincronizado", progress=0.95)
    return report.public()

def job_repair(ctx: JobContext) -> dict[str, Any]:
    ctx.report("revisando torrents con error", progress=0.1)
    return repair_torrents(
        str(ctx.config.db_path),
        _qb_read(ctx.config),
        _qb_write(ctx.config),
        limit=_int(ctx.params.get("limit"), 5),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_clean_broken(ctx: JobContext) -> dict[str, Any]:
    ctx.report("buscando torrents rotos", progress=0.1)
    return clean_broken(
        str(ctx.config.db_path),
        _qb_read(ctx.config),
        _qb_write(ctx.config),
        library_root=ctx.config.catalog_library,
        limit=_int(ctx.params.get("limit"), 10),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_move(ctx: JobContext) -> dict[str, Any]:
    ctx.report("moviendo completados a 1fichier", progress=0.1)
    return move_completed(
        str(ctx.config.db_path),
        _qb_write(ctx.config),
        target=ctx.params.get("target")
        or str(ctx.config.library_root / "descargas"),
        source_root=str(ctx.config.ondemand_save_path),
        grace_seconds=_int(ctx.params.get("grace"), 120),
        limit=_int(ctx.params.get("limit"), 10),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_abandon(ctx: JobContext) -> dict[str, Any]:
    ctx.report("revisando descargas abandonadas", progress=0.1)
    return abandon_idle(
        str(ctx.config.db_path),
        _qb_write(ctx.config),
        limit=_int(ctx.params.get("limit"), 20),
        grace_seconds=_int(ctx.params.get("grace"), 120),
        min_age_seconds=_int(ctx.params.get("min_age"), 600),
        allow_private_delete=_bool(ctx.params.get("allow_private_delete"), False),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_reseed(ctx: JobContext) -> dict[str, Any]:
    ctx.report("reponiendo descargas borradas", progress=0.1)
    return reseed_pending(
        str(ctx.config.db_path),
        _qb_write(ctx.config),
        limit=_int(ctx.params.get("limit"), 1),
        jackett_api_key=ctx.config.jackett_api_key,
        save_path=str(ctx.config.ondemand_save_path),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_evict(ctx: JobContext) -> dict[str, Any]:
    ctx.report("liberando copias locales", progress=0.1)
    return evict_completed(
        str(ctx.config.db_path),
        _qb_write(ctx.config),
        limit=_int(ctx.params.get("limit"), 10),
        grace_seconds=_int(ctx.params.get("grace"), 120),
        roots=tuple(
            dict.fromkeys(
                (
                    str(ctx.config.ondemand_save_path),
                    str(ctx.config.downloads_root / "ondemand"),
                    str(ctx.config.library_root / "descargas"),
                    "/mnt/cache/tmp/ondemand",
                )
            )
        ),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_seeding(ctx: JobContext) -> dict[str, Any]:
    ctx.report("normalizando siembra", progress=0.1)
    return normalize_completed(
        str(ctx.config.db_path),
        _qb_write(ctx.config),
        limit=_int(ctx.params.get("limit"), 50),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_prune(ctx: JobContext) -> dict[str, Any]:
    ctx.report("podando .strm de títulos ya en Radarr/Sonarr", progress=0.1)
    return prune_library(
        str(ctx.config.db_path),
        ctx.config.catalog_library,
        radarr_db=str(ctx.config.radarr_db),
        sonarr_db=str(ctx.config.sonarr_db),
        dry_run=not _bool(ctx.params.get("execute"), False),
    )


def job_library(ctx: JobContext) -> dict[str, Any]:
    ctx.report("sincronizando biblioteca Radarr/Sonarr", progress=0.1)
    return sync_library(
        str(ctx.config.db_path),
        radarr_db=str(ctx.config.radarr_db),
        sonarr_db=str(ctx.config.sonarr_db),
    )


def job_metadata(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    limit = _int(params.get("limit"), 0)
    delay = max(0.0, _float(params.get("delay"), 0.08))
    missing_only = not _bool(params.get("force"), False)
    ctx.report("revisando metadatos pendientes", progress=0.02)
    report = metadata_backfill(
        str(ctx.config.db_path),
        _tmdb(ctx.config),
        limit=limit,
        delay=delay,
        workers=_int(params.get("workers"), 6),
        missing_only=missing_only,
        ctx=ctx,
    )
    ctx.report("metadatos actualizados", progress=0.97)
    return report


def job_debrid(ctx: JobContext) -> dict[str, Any]:
    if not ctx.config.rd_api_key:
        raise JobError("sin RD_API_KEY")
    ctx.report("comprobando caché de Real-Debrid", progress=0.1)
    try:
        client = RealDebridClient.from_config(ctx.config)
    except DebridError as exc:
        raise JobError(str(exc)) from exc
    return cache_public(
        str(ctx.config.db_path),
        client,
        limit=_int(ctx.params.get("limit"), 10),
        delay=max(0.0, _float(ctx.params.get("delay"), 1.0)),
        dry_run=_bool(ctx.params.get("dry_run"), True),
    )


def job_limits(ctx: JobContext) -> dict[str, Any]:
    return apply_qbit_limits(
        ctx.config,
        radarr_kbps=_int(ctx.params.get("radarr_kbps"), DEFAULT_RADARR_KBPS),
        sonarr_kbps=_int(ctx.params.get("sonarr_kbps"), DEFAULT_SONARR_KBPS),
        ondemand_kbps=_int(ctx.params.get("ondemand_kbps"), DEFAULT_ONDEMAND_KBPS),
        dry_run=_bool(ctx.params.get("dry_run"), False),
    )


def job_monitor(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    keys = [str(value) for value in params.get("keys") or [] if value]
    ctx.report("buscando fuentes de títulos en seguimiento", progress=0.02)
    report = monitor_titles(
        str(ctx.config.db_path),
        ctx.config,
        keys=keys or None,
        limit=_int(params.get("limit"), 50),
        delay=max(0.0, _float(params.get("delay"), 2.0)),
        force=_bool(params.get("force"), False),
        ctx=ctx,
    )
    ctx.report("seguimiento terminado", progress=0.97)
    return report


def job_cleanup_leases(ctx: JobContext) -> dict[str, Any]:
    from .stream_leases import cleanup

    removed = cleanup(str(ctx.config.db_path), before_seconds=3600)
    return {"removed": removed}


def job_worker_cycle(ctx: JobContext) -> dict[str, Any]:
    """Ciclo equivalente a `catalogo worker --once`.

    Sustituye al timer de 5 minutos cuando TOD corre como servicio único.
    Respeta los mismos límites y deja en dry-run lo que el worker real
    también deja en dry-run (abandono y normalización de siembra).
    """
    config = ctx.config
    db = str(config.db_path)
    try:
        qbit_read = _qb_read(config)
        qbit_write = _qb_write(config)
    except JobError as exc:
        return {"skipped": "sin_qbit", "error": str(exc)[:200]}

    def cleanup() -> dict:
        from .stream_leases import cleanup as cleanup_leases

        return {"leases": cleanup_leases(db, before_seconds=3600)}

    def sync() -> dict:
        return qbit_sync(
            db,
            qbit_read,
            radarr_db=str(ctx.config.radarr_db),
            sonarr_db=str(ctx.config.sonarr_db),
            check_exists=True,
        ).public()

    def library() -> dict:
        return sync_library(db, radarr_db=str(ctx.config.radarr_db), sonarr_db=str(ctx.config.sonarr_db))

    def repair() -> dict:
        return repair_torrents(
            db, qbit_read, qbit_write, limit=5, dry_run=False
        )

    def reseed() -> dict:
        return reseed_pending(
            db,
            qbit_write,
            limit=1,
            jackett_api_key=config.jackett_api_key,
            save_path=str(config.ondemand_save_path),
            dry_run=False,
        )

    def clean() -> dict:
        return clean_broken(
            db,
            qbit_read,
            qbit_write,
            library_root=config.catalog_library,
            limit=10,
            dry_run=False,
        )

    def moves() -> dict:
        return move_completed(
            db,
            qbit_write,
            source_root=str(config.ondemand_save_path),
            target=str(config.library_root / "descargas"),
            limit=1,
            dry_run=False,
        )

    def abandon() -> dict:
        return abandon_idle(db, qbit_write, limit=10, dry_run=True)

    def seeding() -> dict:
        return normalize_completed(db, qbit_write, limit=50, dry_run=True)

    return run_once(
        cleanup=cleanup,
        sync=sync,
        library=library,
        repair=repair,
        reseed=reseed,
        clean=clean,
        moves=moves,
        abandon=abandon,
        seeding=seeding,
    ).public()


def job_bridge(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    try:
        radarr = RadarrClient.from_config(ctx.config)
        sonarr = SonarrClient.from_config(ctx.config)
    except Exception as exc:
        return {"skipped": "sin_arr", "error": str(exc)[:200]}
    return bridge_downloads(
        str(ctx.config.db_path),
        radarr,
        sonarr,
        limit=_int(params.get("limit"), 1),
        dry_run=not _bool(params.get("execute"), True),
    )


def job_restore_strm(ctx: JobContext) -> dict[str, Any]:
    params = ctx.params
    raw_years = str(params.get("years") or "2020,2021,2022,2023,2024,2025,2026")
    years = [
        int(part.strip())
        for part in raw_years.split(",")
        if part.strip().lstrip("-").isdigit()
    ]
    out = str(ctx.config.catalog_library)
    report: dict[str, Any] = {}
    for year in years:
        ctx.check()
        report[f"movie_{year}"] = export_strm(
            ctx.config.db_path,
            out,
            base_url=ctx.config.playback_base_url,
            limit=None,
            year=year,
            kind="movie",
            skip_library=False,
            radarr_db=str(ctx.config.radarr_db),
            sonarr_db=str(ctx.config.sonarr_db),
        )
    report["series"] = export_strm(
        ctx.config.db_path,
        out,
        base_url=ctx.config.playback_base_url,
        limit=None,
        kind="series",
        skip_library=False,
        radarr_db=str(ctx.config.radarr_db),
        sonarr_db=str(ctx.config.sonarr_db),
    )
    return report


JOB_HANDLERS = {
    "health": job_health,
    "sync-sources": job_sync_sources,
    "rss-sync": job_rss,
    "backfill": job_backfill,
    "export-strm": job_export,
    "qbit-sync": job_qbit_sync,
    "repair": job_repair,
    "clean-broken": job_clean_broken,
    "move-completed": job_move,
    "abandon": job_abandon,
    "reseed": job_reseed,
    "evict": job_evict,
    "seeding": job_seeding,
    "prune-library": job_prune,
    "sync-library": job_library,
    "metadata": job_metadata,
    "monitor": job_monitor,
    "debrid-check": job_debrid,
    "qbit-limits": job_limits,
    "cleanup-leases": job_cleanup_leases,
    "worker-cycle": job_worker_cycle,
    "bridge": job_bridge,
    "restore-strm": job_restore_strm,
}
