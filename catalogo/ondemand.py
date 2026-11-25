"""Vista del catálogo on-demand.

Los estados se calculan con hechos:
- `.strm` existente en disco -> publicado.
- torrent de categoría ondemand en qBittorrent -> descarga real.
- Radarr/Sonarr -> si el título ya está en biblioteca (solo lectura).
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any

from . import queries, settings, strm_inventory
from .db import connect

PAGE_SIZE = 25
_OVERVIEW_TTL = 10.0
_OVERVIEW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CATALOG_TTL = 30.0
_CATALOG_CACHE: dict[tuple[str, str, bool, bool], tuple[float, list[dict[str, Any]]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _asset_ids_with_releases(db_path) -> set[int]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT DISTINCT asset_id FROM releases").fetchall()
    return {int(row["asset_id"]) for row in rows}


def _asset_ids_with_downloads(db_path) -> set[int]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT asset_id FROM torrent_files
            WHERE asset_id IS NOT NULL
              AND (
                (storage_location='LOCAL_DISK' AND state IN ('ready','seeding'))
                OR (storage_location='1FICHIER' AND state='archived')
              )
            """
        ).fetchall()
    return {int(row["asset_id"]) for row in rows}


def _asset_ids_with_library(db_path) -> set[int]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.id FROM media_items m
            JOIN library_files lf
              ON lf.kind = m.kind AND lf.tmdb = m.tmdb
             AND (m.kind = 'movie'
                  OR (lf.season = m.season AND lf.episode = m.episode))
            """
        ).fetchall()
    return {int(row["id"]) for row in rows}


def downloaded_asset_ids(db_path) -> set[int]:
    return _asset_ids_with_downloads(db_path)


def _ondemand_torrents(db_path, qbit=None) -> dict[int, dict[str, Any]]:
    """asset_id -> último torrent ondemand que lo contiene."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT f.asset_id, t.info_hash, t.state, t.qbit_state,
                   t.progress, t.ratio, t.id AS torrent_id
            FROM torrent_files f
            JOIN torrents t ON t.id = f.torrent_id
            WHERE t.category IN ('ondemand', 'radarr', 'tv-sonarr')
              AND t.state <> 'removed'
              AND f.asset_id IS NOT NULL
            ORDER BY t.id
            """
        ).fetchall()
    live: dict[str, dict] = {}
    if qbit is not None:
        try:
            for torrent in qbit.torrents(category="ondemand"):
                live[(torrent.get("hash") or "").lower()] = torrent
        except Exception:
            live = {}
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        info_hash = (item.get("info_hash") or "").lower()
        current = live.get(info_hash)
        if current is not None:
            item["state"] = current.get("state") or item.get("state")
            item["progress"] = current.get("progress") or 0
        result[int(item["asset_id"])] = item
    return result


def _download_state(torrent: dict[str, Any] | None) -> tuple[str, float]:
    if not torrent:
        return "sin_descarga", 0.0
    raw = (torrent.get("state") or "").lower()
    progress = float(torrent.get("progress") or 0)
    if "error" in raw or "missing" in raw:
        return "error", progress
    if progress >= 1 or "seed" in raw or "upload" in raw or raw.endswith("up"):
        return "sembrando", progress
    if raw.startswith(("paused", "stopped")):
        return "pausado", progress
    if "dl" in raw or "download" in raw or "meta" in raw:
        return "descargando", progress
    return "sin_descarga", progress


def _summary_states(db_path, config, qbit=None) -> dict[str, Any]:
    inventory = strm_inventory.inventory(config.catalog_library)
    keys = inventory["keys"]
    with_releases = _asset_ids_with_releases(db_path)
    with_downloads = _asset_ids_with_downloads(db_path)
    with_library = _asset_ids_with_library(db_path)
    with closing(connect(db_path)) as conn:
        media = conn.execute(
            """
            SELECT id, key, kind, tmdb, title, year, season, episode,
                   COALESCE(popularity,0) AS popularity
            FROM media_items
            """
        ).fetchall()

    movies = {"published": 0, "pending": 0, "no_source": 0, "total": 0}
    series: dict[int, dict[str, Any]] = {}
    published_episodes = 0
    db_keys: set[str] = set()
    for row in media:
        item = dict(row)
        db_keys.add(item["key"])
        published = strm_inventory.is_key_published(item, keys)
        has_release = (
            int(item["id"]) in with_releases
            or int(item["id"]) in with_downloads
            or int(item["id"]) in with_library
        )
        if item["kind"] == "movie":
            movies["total"] += 1
            if published:
                movies["published"] += 1
            elif has_release:
                movies["pending"] += 1
            else:
                movies["no_source"] += 1
            continue
        tmdb = int(item["tmdb"] or 0)
        group = series.setdefault(
            tmdb,
            {"published": False, "has_release": False, "episodes": 0},
        )
        group["episodes"] += 1
        group["has_release"] = group["has_release"] or has_release
        if published:
            group["published"] = True
            published_episodes += 1

    series_counts = {"published": 0, "pending": 0, "no_source": 0, "total": len(series)}
    for group in series.values():
        if group["published"]:
            series_counts["published"] += 1
        elif group["has_release"]:
            series_counts["pending"] += 1
        else:
            series_counts["no_source"] += 1

    return {
        "movies": movies,
        "series": series_counts,
        "episodes": published_episodes,
        "orphans": len(keys - db_keys),
        "inventory": inventory,
    }


def _download_counts(qbit) -> dict[str, int]:
    counts = {
        "total": 0,
        "downloading": 0,
        "seeding": 0,
        "paused": 0,
        "error": 0,
        "other": 0,
    }
    if qbit is None:
        return counts
    try:
        torrents = qbit.torrents(category="ondemand")
    except Exception:
        return counts
    for torrent in torrents:
        state, _ = _download_state(torrent)
        counts["total"] += 1
        key = {
            "descargando": "downloading",
            "sembrando": "seeding",
            "pausado": "paused",
            "error": "error",
        }.get(state, "other")
        counts[key] += 1
    return counts


def public_arr(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if key not in ("movies_by_tmdb", "series_by_tmdb")
    }


def overview(
    db_path,
    config,
    *,
    qbit=None,
    arr=None,
) -> dict[str, Any]:
    cache_key = f"{db_path}:{int(qbit is not None)}:{int(arr is not None)}"
    cached = _OVERVIEW_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _OVERVIEW_TTL:
        return cached[1]
    states = _summary_states(db_path, config, qbit=qbit)
    downloads = _download_counts(qbit)
    library = public_arr(arr.overview() if arr is not None else {
        "ok": False,
        "error": "Radarr/Sonarr no configurados",
    })
    recent = library.get("recent") or []
    if recent:
        with closing(connect(db_path)) as conn:
            for item in recent:
                tmdb = item.get("tmdb")
                if not tmdb:
                    continue
                if item.get("kind") == "movie":
                    row = conn.execute(
                        "SELECT id FROM media_items WHERE kind='movie' "
                        "AND tmdb = ? ORDER BY id LIMIT 1",
                        (int(tmdb),),
                    ).fetchone()
                    if row is not None:
                        item["asset_id"] = int(row["id"])
                elif item.get("kind") == "series":
                    item["series_tmdb"] = int(tmdb)
    limit = settings.ondemand_max_active(
        db_path, getattr(config, "max_active_ondemand", settings.DEFAULT_MAX_ACTIVE)
    )
    disk = library.get("disk") if library.get("ok") else None
    if not disk:
        disk = queries._disk(config.catalog_library)
    payload = {
        "catalog": {
            "movies": states["movies"],
            "series": states["series"],
            "episodes": states["episodes"],
            "orphans": states.get("orphans", 0),
            "versions": states["inventory"]["versions"],
            "root": states["inventory"]["root"],
        },
        "downloads": downloads,
        "limit": limit,
        "library": library,
        "disk": disk,
        "generated_at": _now(),
    }
    _OVERVIEW_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def recent_published(
    db_path,
    config,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    """Últimas incorporaciones que ya están en Jellyfin, sin recorrer el catálogo."""
    limit = max(1, min(int(limit or 6), 24))
    keys = strm_inventory.inventory(config.catalog_library)["keys"]
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, key, kind, tmdb, title, year, poster_path, updated_at "
            "FROM media_items ORDER BY updated_at DESC, id DESC LIMIT 400"
        ).fetchall()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        item = dict(row)
        if item.get("key") not in keys:
            continue
        group = (
            ("series", int(item["tmdb"] or 0))
            if item.get("kind") == "series"
            else ("movie", int(item["id"]))
        )
        if group in seen:
            continue
        seen.add(group)
        items.append(item)
        if len(items) >= limit:
            break
    return {"items": items}



def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _norm_quality_label(q: str | None) -> str:
    txt = str(q or "").strip().lower()
    if txt in ("4k", "uhd", "2160p"):
        return "2160p"
    if txt in ("1080p", "1080", "fullhd"):
        return "1080p"
    if txt in ("720p", "720", "hd"):
        return "720p"
    if txt in ("480p", "sd", "dvdrip"):
        return "480p"
    return txt


def _catalog_extra_maps(db_path) -> dict[str, Any]:
    from . import lists as lists_mod, naming as naming_mod, profiles as profiles_mod

    prof_list = profiles_mod.list_profiles(db_path)
    prof_by_id = {int(p["id"]): p for p in prof_list}
    default_prof = next((p for p in prof_list if p.get("is_default")), prof_list[0] if prof_list else None)
    rf_list = naming_mod.list_root_folders(db_path)
    rf_by_id = {int(r["id"]): r for r in rf_list}
    tags_map = lists_mod.media_tags_map(db_path)

    best_rel: dict[int, dict[str, Any]] = {}
    asset_trackers: dict[int, set[str]] = {}
    asset_privacies: dict[int, set[str]] = {}
    disk_bytes: dict[int, int] = {}
    ss_map: dict[int, dict[str, Any]] = {}

    with closing(connect(db_path)) as conn:
        for row in conn.execute(
            """
            SELECT r.asset_id, r.quality, r.audio, r.tracker, r.privacy,
                   r.source_type, r.size_bytes, r.seeders
            FROM releases r
            WHERE r.health = 'alive'
              AND NOT EXISTS (
                  SELECT 1 FROM tracker_policies tp
                  WHERE tp.tracker = r.tracker AND tp.enabled = 0
              )
              AND NOT EXISTS (
                  SELECT 1 FROM stream_failures sf
                  WHERE sf.release_id = r.id
                    AND sf.until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
              )
            ORDER BY COALESCE(r.size_bytes, 999999999999) ASC, COALESCE(r.seeders, 0) DESC
            """
        ).fetchall():
            aid = int(row["asset_id"])
            if row["tracker"]:
                asset_trackers.setdefault(aid, set()).add(str(row["tracker"]))
            if row["privacy"]:
                asset_privacies.setdefault(aid, set()).add(str(row["privacy"]))
            if aid not in best_rel:
                best_rel[aid] = dict(row)
        for row in conn.execute(
            """
            SELECT asset_id, COALESCE(SUM(size_bytes), 0) AS total_bytes
            FROM torrent_files
            WHERE asset_id IS NOT NULL
              AND storage_location = 'LOCAL_DISK'
              AND state IN ('ready', 'seeding', 'complete')
            GROUP BY asset_id
            """
        ).fetchall():
            disk_bytes[int(row["asset_id"])] = int(row["total_bytes"] or 0)
        if _table_exists(conn, "series_settings"):
            for row in conn.execute("SELECT * FROM series_settings").fetchall():
                ss_map[int(row["tmdb"])] = dict(row)

    return {
        "prof_by_id": prof_by_id,
        "default_prof": default_prof,
        "rf_by_id": rf_by_id,
        "tags_map": tags_map,
        "best_rel": best_rel,
        "asset_trackers": asset_trackers,
        "asset_privacies": asset_privacies,
        "disk_bytes": disk_bytes,
        "ss_map": ss_map,
    }


def _catalog_base(db_path, config) -> list[dict[str, Any]]:
    """Estado calculado de todo el catálogo, sin filtros de texto ni tipo."""
    inventory = strm_inventory.inventory(config.catalog_library)
    keys = inventory["keys"]
    with_releases = _asset_ids_with_releases(db_path)
    downloaded_assets = _asset_ids_with_downloads(db_path)
    with_library = _asset_ids_with_library(db_path)
    # La lista usa el último estado sincronizado de qBittorrent: no bloquea
    # la lectura del catálogo con una llamada de red.
    torrents = _ondemand_torrents(db_path)
    extra = _catalog_extra_maps(db_path)
    prof_by_id = extra["prof_by_id"]
    default_prof = extra["default_prof"]
    rf_by_id = extra["rf_by_id"]
    tags_map = extra["tags_map"]
    best_rel_map = extra["best_rel"]
    asset_trackers = extra["asset_trackers"]
    asset_privacies = extra["asset_privacies"]
    disk_bytes_map = extra["disk_bytes"]
    ss_map = extra["ss_map"]

    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.key, m.kind, m.tmdb, m.title, m.year, m.season,
                   m.episode, m.status, m.updated_at, m.created_at,
                   m.library_added_at, m.poster_path, m.overview,
                   m.vote_average, m.library_added, m.release_date, m.air_date,
                   m.monitored, m.origin, m.origin_detail, m.origin_at,
                   m.quality_profile_id, m.root_folder_id, m.genres,
                   m.series_status, m.series_type, m.is_anime,
                   COALESCE(m.popularity,0) AS popularity,
                   (SELECT COUNT(*) FROM releases r WHERE r.asset_id=m.id)
                       AS releases,
                   (SELECT COUNT(*) FROM releases r WHERE r.asset_id=m.id
                       AND r.health='alive') AS alive
            FROM media_items m
            """
        ).fetchall()

    base: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        published = strm_inventory.is_key_published(item, keys)
        # `library_added` significa estar en Radarr/Sonarr. Publicado es
        # tener .strm. Son dos estados distintos y no se mezclan.
        library_added = bool(item.get("library_added"))
        torrent = torrents.get(int(item["id"]))
        dl_state, progress = _download_state(torrent)
        # La biblioteca de Radarr/Sonarr se lee del espejo local
        # (`library_files`): la lista no debe hacer 655 peticiones HTTP.
        has_file = int(item["id"]) in with_library
        if has_file:
            library_added = True
        downloaded = (
            dl_state == "sembrando"
            or int(item["id"]) in downloaded_assets
            or has_file
        )
        if published:
            pub_state = "publicado"
        elif downloaded or int(item["id"]) in with_releases:
            pub_state = "pendiente"
        else:
            pub_state = "sin_fuente"
        available, _ = _release_available(
            item.get("release_date") or item.get("air_date")
        )
        is_special = item["kind"] == "series" and int(item.get("season") or 0) == 0
        incomplete_item = bool(
            library_added
            and not downloaded
            and int(item.get("alive") or 0) == 0
            and available
            and not is_special
        )
        aid = int(item["id"])
        ss = ss_map.get(int(item["tmdb"])) if item.get("kind") == "series" and item.get("tmdb") else None
        eff_qp_id = item.get("quality_profile_id") or (ss.get("quality_profile_id") if ss else None)
        eff_prof = prof_by_id.get(int(eff_qp_id)) if eff_qp_id and int(eff_qp_id) in prof_by_id else default_prof
        eff_rf_id = item.get("root_folder_id") or (ss.get("root_folder_id") if ss else None)
        eff_rf = rf_by_id.get(int(eff_rf_id)) if eff_rf_id and int(eff_rf_id) in rf_by_id else None
        best_r = best_rel_map.get(aid)
        best_q = _norm_quality_label(best_r.get("quality")) if best_r else None
        best_a = str(best_r.get("audio") or "").lower() if best_r else None
        cutoff_unmet = False
        if best_r and eff_prof and eff_prof.get("cutoff_quality") and not has_file:
            order = eff_prof.get("quality_order") or []
            norm_order = [_norm_quality_label(x) for x in order]
            cq = _norm_quality_label(eff_prof["cutoff_quality"])
            if cq in norm_order and best_q in norm_order:
                cutoff_unmet = norm_order.index(best_q) > norm_order.index(cq)
        t_key = (item.get("kind"), int(item["tmdb"])) if item.get("tmdb") else None
        item_tags = tags_map.get(t_key, []) if t_key else []

        item.update(
            {
                "profile_id": eff_prof.get("id") if eff_prof else None,
                "profile_name": eff_prof.get("name") if eff_prof else None,
                "root_folder_id": eff_rf.get("id") if eff_rf else None,
                "root_folder_name": eff_rf.get("name") if eff_rf else None,
                "tags": item_tags,
                "best_quality": best_q,
                "best_audio": best_a,
                "best_source_type": best_r.get("source_type") if best_r else None,
                "trackers": sorted(asset_trackers.get(aid, set())),
                "privacies": sorted(asset_privacies.get(aid, set())),
                "disk_bytes": disk_bytes_map.get(aid, 0),
                "cutoff_unmet": cutoff_unmet,
                "publication": pub_state,
                "published": published,
                "library_added": library_added,
                "download": dl_state,
                "downloaded": downloaded,
                "incomplete": incomplete_item,
                "progress": progress,
                "in_library": library_added,
                "has_file": has_file,
                "available": available,
                "recent_at": (
                    item.get("library_added_at")
                    or item.get("created_at")
                    or item.get("updated_at")
                    or ""
                ),
            }
        )
        base.append(item)
    return base


def invalidate_catalog() -> None:
    """Una acción del usuario cambia el catálogo; no esperar al TTL."""
    _CATALOG_CACHE.clear()


def invalidate_caches() -> None:
    """Invalida catálogo y resumen tras una mutación del usuario."""
    _CATALOG_CACHE.clear()
    _OVERVIEW_CACHE.clear()


def _catalog_base_cached(db_path, config) -> list[dict[str, Any]]:
    key = (str(db_path), str(config.catalog_library))
    now = time.monotonic()
    cached = _CATALOG_CACHE.get(key)
    if cached is not None and now - cached[0] < _CATALOG_TTL:
        return [dict(item) for item in cached[1]]
    base = _catalog_base(db_path, config)
    _CATALOG_CACHE[key] = (now, base)
    return [dict(item) for item in base]


def catalog(
    db_path,
    config,
    *,
    qbit=None,
    arr=None,
    q: str = "",
    kind: str = "",
    publication: str = "",
    download: str = "",
    library: str = "",
    origin: str = "",
    incomplete: bool = False,
    profile_id: int | None = None,
    quality: str = "",
    audio: str = "",
    monitored: str = "",
    tracker: str = "",
    privacy: str = "",
    tag: str = "",
    genre: str = "",
    series_status: str = "",
    root_folder: str = "",
    year_min: int | None = None,
    year_max: int | None = None,
    cutoff_unmet: bool = False,
    custom_filter_id: int | None = None,
    page: int = 1,
    per_page: int = PAGE_SIZE,
    group: str = "episode",
    facets: bool = True,
    sort: str = "popularity",
) -> dict[str, Any]:
    """Catálogo paginado.

    `group=series` colapsa los episodios en una entrada por serie. Es el modo
    que usa la interfaz: una serie no debe ocupar 13 filas seguidas.
    """
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 100))
    group = "series" if group == "series" else "episode"
    # La lista se calcula desde la base local y se cachea 30 s; `qbit` y
    # `arr` se mantienen en la firma por compatibilidad con las rutas.
    if custom_filter_id:
        from . import lists as lists_mod
        cf = next((f for f in lists_mod.list_custom_filters(db_path) if f["id"] == int(custom_filter_id)), None)
        if cf and isinstance(cf.get("filters"), dict):
            flt = cf["filters"]
            q = q or str(flt.get("q") or "")
            kind = kind or str(flt.get("kind") or "")
            publication = publication or str(flt.get("publication") or "")
            download = download or str(flt.get("download") or "")
            library = library or str(flt.get("library") or "")
            origin = origin or str(flt.get("origin") or "")
            incomplete = incomplete or bool(flt.get("incomplete"))
            profile_id = profile_id or (int(flt["profile_id"]) if flt.get("profile_id") else None)
            quality = quality or str(flt.get("quality") or "")
            audio = audio or str(flt.get("audio") or "")
            monitored = monitored or str(flt.get("monitored") or "")
            tracker = tracker or str(flt.get("tracker") or "")
            privacy = privacy or str(flt.get("privacy") or "")
            tag = tag or str(flt.get("tag") or "")
            genre = genre or str(flt.get("genre") or "")
            series_status = series_status or str(flt.get("series_status") or "")
            root_folder = root_folder or str(flt.get("root_folder") or "")
            cutoff_unmet = cutoff_unmet or bool(flt.get("cutoff_unmet"))
    base = _catalog_base_cached(db_path, config)
    text = (q or "").strip().lower()
    if text:
        base = [
            item
            for item in base
            if text in (item.get("title") or "").lower()
            or text in (item.get("key") or "").lower()
        ]
    if kind in ("movie", "series"):
        base = [item for item in base if item["kind"] == kind]

    if group == "series":
        grouped = _group_series(base)
        grouped.sort(key=lambda item: (
            -(float(item.get("popularity") or 0)),
            (item.get("title") or "").lower(),
        ))
        entries = grouped
        episodes_total = sum(
            int(item.get("episodes") or 0)
            for item in entries
            if item["kind"] == "series"
        )
        episodes_published_total = sum(
            int(item.get("published_episodes") or 0)
            for item in entries
            if item["kind"] == "series"
        )
        episodes_library_total = sum(
            int(item.get("library_episodes") or 0)
            for item in entries
            if item["kind"] == "series"
        )
    else:
        entries = base
        episodes_total = sum(
            1 for item in entries if item["kind"] == "series"
        )
        episodes_published_total = sum(
            1 for item in entries
            if item["kind"] == "series" and item.get("published")
        )
        episodes_library_total = sum(
            1 for item in entries
            if item["kind"] == "series" and item.get("library_added")
        )

    # Las facetas cuentan sobre el catálogo ya agrupado, antes de aplicar
    # los filtros: así el mismo concepto muestra el mismo número en el menú,
    # la tarjeta y el filtro (DESIGN.md). `q` y el tipo sí acotan el universo;
    # el estado elegido no borra los demás estados.
    facet_data = None
    if facets:
        all_facets = _facets(entries)
        scoped = [
            item for item in entries
            if not kind or item["kind"] == kind
        ]
        facet_data = _facets(scoped)
        facet_data["kind"] = all_facets["kind"]

    def keep(item: dict[str, Any]) -> bool:
        if origin and item.get("origin") != origin:
            return False
        if publication and item.get("publication") != publication:
            return False
        if download:
            if item["kind"] == "series":
                counts = item.get("download_counts") or {}
                if download == "descargado":
                    if not counts.get("descargado"):
                        return False
                elif download == "sin_descarga":
                    if counts.get("descargado") or counts.get("descargando") \
                            or counts.get("pausado") or counts.get("error"):
                        return False
                elif download in ("descargando", "pausado", "error"):
                    if not counts.get(download):
                        return False
                elif item.get("download") != download:
                    return False
            elif download == "descargado":
                if not item.get("downloaded"):
                    return False
            elif download == "sin_descarga":
                if item.get("downloaded") or item.get("download") in (
                    "descargando", "pausado", "error", "sembrando"
                ):
                    return False
            elif item.get("download") != download:
                return False
        if library == "yes" and not item.get("in_library"):
            return False
        if library == "no" and item.get("in_library"):
            return False
        if incomplete and not item.get("incomplete"):
            return False
        if profile_id and int(item.get("profile_id") or 0) != int(profile_id):
            return False
        if quality:
            qnorm = _norm_quality_label(quality)
            if _norm_quality_label(item.get("best_quality")) != qnorm:
                return False
        if audio and str(item.get("best_audio") or "").lower() != audio.lower():
            return False
        if monitored in ("1", "true", "yes") and not item.get("monitored"):
            return False
        if monitored in ("0", "false", "no") and item.get("monitored"):
            return False
        if tracker and tracker not in (item.get("trackers") or []):
            return False
        if privacy and privacy not in (item.get("privacies") or []):
            return False
        if tag:
            t_low = tag.lower()
            tags_list = item.get("tags") or []
            if not any(str(t.get("name", "")).lower() == t_low or str(t.get("id")) == tag for t in tags_list):
                return False
        if genre:
            if genre.lower() not in str(item.get("genres") or "").lower():
                return False
        if series_status:
            if series_status.lower() not in str(item.get("series_status") or "").lower():
                return False
        if root_folder:
            if str(item.get("root_folder_id") or "") != str(root_folder) and str(item.get("root_folder_name") or "").lower() != root_folder.lower():
                return False
        if year_min and int(item.get("year") or 0) < int(year_min):
            return False
        if year_max and int(item.get("year") or 9999) > int(year_max):
            return False
        if cutoff_unmet and not item.get("cutoff_unmet"):
            return False
        return True

    filtered = [item for item in entries if keep(item)]
    if sort == "title":
        filtered.sort(key=lambda item: (item.get("title") or "").lower())
    elif sort == "year":
        filtered.sort(
            key=lambda item: (-(int(item.get("year") or 0)),
                              (item.get("title") or "").lower())
        )
    elif sort == "recent":
        filtered.sort(
            key=lambda item: str(item.get("recent_at") or ""), reverse=True
        )

    total = len(filtered)
    start = (page - 1) * per_page
    page_items = filtered[start:start + per_page]
    return {
        "items": page_items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, (total + per_page - 1) // per_page),
        "group": group,
        "episodes_total": episodes_total,
        "episodes_published_total": episodes_published_total,
        "episodes_library_total": episodes_library_total,
        "facets": facet_data,
        "generated_at": _now(),
    }


def _release_available(value: str | None) -> tuple[bool, str | None]:
    """Disponible cuando la fecha de estreno/emisión ya ha llegado."""
    text = (value or "").strip()[:10]
    if not text:
        return True, None
    return text <= date.today().isoformat(), text


def _group_series(items) -> list[dict[str, Any]]:
    """Colapsa episodios en una entrada por serie, con contadores."""
    groups: dict[int, list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for item in items:
        if item["kind"] != "series" or not item.get("tmdb"):
            output.append(item)
            continue
        groups.setdefault(int(item["tmdb"]), []).append(item)

    for tmdb, rows in groups.items():
        rows.sort(
            key=lambda row: (
                int(row.get("season") or 0),
                int(row.get("episode") or 0),
            )
        )
        first = rows[0]
        first["recent_at"] = max(
            (str(row.get("recent_at") or "") for row in rows),
            default="",
        )
        regular_rows = [
            row for row in rows if int(row.get("season") or 0) != 0
        ]
        special_rows = [
            row for row in rows if int(row.get("season") or 0) == 0
        ]
        aired_rows = [
            row for row in regular_rows if row.get("available") is not False
        ]
        aired_count = len(aired_rows)
        published_aired = sum(1 for row in aired_rows if row.get("published"))
        upcoming = len(regular_rows) - aired_count
        counts = {
            "descargado": 0,
            "descargando": 0,
            "pausado": 0,
            "error": 0,
            "sin_descarga": 0,
        }
        published_count = 0
        library_count = 0
        incomplete_count = 0
        for row in rows:
            if row.get("published"):
                published_count += 1
            if row.get("library_added"):
                library_count += 1
            if row.get("incomplete"):
                incomplete_count += 1
            if row.get("downloaded"):
                counts["descargado"] += 1
                continue
            state = row.get("download") or "sin_descarga"
            if state == "sembrando":
                counts["descargado"] += 1
            elif state in ("descargando", "pausado", "error"):
                counts[state] += 1
            else:
                counts["sin_descarga"] += 1
        if counts["descargando"]:
            primary = "descargando"
        elif counts["pausado"]:
            primary = "pausado"
        elif counts["error"]:
            primary = "error"
        elif counts["descargado"]:
            primary = "sembrando"
        else:
            primary = "sin_descarga"
        publication = (
            "publicado"
            if published_count
            else "pendiente"
            if any(row.get("publication") == "pendiente" for row in rows)
            else "sin_fuente"
        )
        poster = next(
            (row.get("poster_path") for row in rows if row.get("poster_path")),
            None,
        )
        overview = next(
            (row.get("overview") for row in rows if row.get("overview")), None
        )
        vote = next(
            (row.get("vote_average") for row in rows
             if row.get("vote_average") is not None),
            None,
        )
        output.append(
            {
                "id": first["id"],
                "key": f"series:tmdb:{tmdb}",
                "kind": "series",
                "tmdb": tmdb,
                "title": first.get("title"),
                "year": first.get("year"),
                "season": None,
                "episode": None,
                "status": first.get("status"),
                "updated_at": max(
                    (str(row.get("updated_at") or "") for row in rows),
                    default="",
                ),
                "popularity": max(
                    float(row.get("popularity") or 0) for row in rows
                ),
                "releases": sum(int(row.get("releases") or 0) for row in rows),
                "alive": sum(int(row.get("alive") or 0) for row in rows),
                "publication": publication,
                "published": bool(published_count),
                "library_added": bool(library_count),
                "library_episodes": library_count,
                "download": primary,
                "downloaded": bool(counts["descargado"]),
                "progress": max(
                    (float(row.get("progress") or 0) for row in rows),
                    default=0.0,
                ),
                "in_library": any(bool(row.get("in_library")) for row in rows),
                "has_file": any(bool(row.get("has_file")) for row in rows),
                "episodes": len(rows),
                "published_episodes": published_count,
                "regular_episodes": len(regular_rows),
                "special_episodes": len(special_rows),
                "aired_episodes": aired_count,
                "published_aired": published_aired,
                "upcoming_episodes": upcoming,
                "download_counts": counts,
                "incomplete": incomplete_count > 0,
                "incomplete_episodes": incomplete_count,
                "seasons": sorted(
                    {int(row.get("season") or 0) for row in rows if row.get("season")}
                ),
                "poster_path": poster,
                "overview": overview,
                "vote_average": vote,
                "origin": (
                    first.get("origin")
                    if all(row.get("origin") == first.get("origin") for row in rows)
                    else "mixed"
                ),
                "profile_id": first.get("profile_id"),
                "profile_name": first.get("profile_name"),
                "root_folder_id": first.get("root_folder_id"),
                "root_folder_name": first.get("root_folder_name"),
                "genres": first.get("genres"),
                "series_status": first.get("series_status"),
                "series_type": first.get("series_type") or "standard",
                "tags": first.get("tags") or [],
                "monitored": any(bool(row.get("monitored")) for row in rows),
                "best_quality": next((row.get("best_quality") for row in rows if row.get("best_quality")), None),
                "best_audio": next((row.get("best_audio") for row in rows if row.get("best_audio")), None),
                "trackers": sorted({t for row in rows for t in (row.get("trackers") or [])}),
                "privacies": sorted({p for row in rows for p in (row.get("privacies") or [])}),
                "disk_bytes": sum(int(row.get("disk_bytes") or 0) for row in rows),
                "cutoff_unmet": any(bool(row.get("cutoff_unmet")) for row in rows),
                "next_air_date": min(
                    (
                        str(row.get("air_date"))[:10]
                        for row in regular_rows
                        if row.get("available") is False and row.get("air_date")
                    ),
                    default=None,
                ),
                "origin_detail": first.get("origin_detail"),
                "origin_at": max(
                    (str(row.get("origin_at") or "") for row in rows),
                    default="",
                ),
                "recent_at": first.get("recent_at"),
                "library_added_at": max(
                    (str(row.get("library_added_at") or "") for row in rows),
                    default="",
                ) or None,
            }
        )
    return output


def _facets(items: list[dict[str, Any]]) -> dict[str, Any]:
    publication = {"publicado": 0, "pendiente": 0, "sin_fuente": 0}
    downloads = {
        "descargando": 0,
        "pausado": 0,
        "error": 0,
        "descargado": 0,
        "sin_descarga": 0,
    }
    kinds = {"movie": 0, "series": 0}
    origins: dict[str, int] = {}
    incomplete = 0
    for item in items:
        publication[item.get("publication") or "sin_fuente"] = (
            publication.get(item.get("publication") or "sin_fuente", 0) + 1
        )
        origin = item.get("origin") or "legacy"
        origins[origin] = origins.get(origin, 0) + 1
        if item.get("kind") in kinds:
            kinds[item["kind"]] += 1
        if item.get("incomplete"):
            incomplete += 1
        if item["kind"] == "series":
            counts = item.get("download_counts") or {}
            if counts.get("descargando"):
                downloads["descargando"] += 1
            elif counts.get("pausado"):
                downloads["pausado"] += 1
            elif counts.get("error"):
                downloads["error"] += 1
            elif counts.get("descargado"):
                downloads["descargado"] += 1
            else:
                downloads["sin_descarga"] += 1
        else:
            state = item.get("download") or "sin_descarga"
            if state == "sembrando":
                downloads["descargado"] += 1
            elif state in downloads:
                downloads[state] += 1
            else:
                downloads["sin_descarga"] += 1
    return {
        "publication": publication,
        "download": downloads,
        "kind": kinds,
        "origin": dict(
            sorted(origins.items(), key=lambda item: item[1], reverse=True)
        ),
        "incomplete": incomplete,
    }


def series_detail(
    db_path,
    config,
    tmdb: int,
    *,
    qbit=None,
    arr=None,
    tmdb_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Serie completa, cruzando TMDB con lo que existe en el catálogo.

    TMDB manda para la estructura (temporadas y episodios). La base local
    aporta el estado real de cada episodio: catálogo, `.strm`, descarga,
    releases y salud. Los episodios que aún no están en la base aparecen
    marcados como ``sin_catalogo``.
    """
    tmdb = int(tmdb)
    inventory = strm_inventory.inventory(config.catalog_library)
    keys = inventory["keys"]
    with_releases = _asset_ids_with_releases(db_path)
    downloaded_assets = _asset_ids_with_downloads(db_path)
    with_library = _asset_ids_with_library(db_path)
    # La lista usa el último estado sincronizado de qBittorrent: no bloquea
    # la lectura del catálogo con una llamada de red.
    torrents = _ondemand_torrents(db_path)
    with closing(connect(db_path)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, key, kind, tmdb, title, year, season, episode,
                       status, updated_at, poster_path, overview, vote_average,
                       library_added, monitored, air_date, episode_title,
                       episode_overview
                FROM media_items
                WHERE kind='series' AND tmdb=?
                ORDER BY season, episode, id
                """,
                (tmdb,),
            ).fetchall()
        ]
        release_counts = {
            int(row["asset_id"]): (int(row["releases"]), int(row["alive"]))
            for row in conn.execute(
                """
                SELECT asset_id, COUNT(*) AS releases,
                       SUM(CASE WHEN health='alive' THEN 1 ELSE 0 END) AS alive
                FROM releases
                WHERE asset_id IN (SELECT id FROM media_items
                                   WHERE kind='series' AND tmdb=?)
                GROUP BY asset_id
                """,
                (tmdb,),
            ).fetchall()
        }
    if not rows and not tmdb_data:
        return None

    lib = (
        arr.in_library("series", tmdb)
        if arr is not None
        else {"in_library": False, "has_file": False}
    )

    def state_for(row: dict[str, Any]) -> dict[str, Any]:
        published = strm_inventory.is_key_published(row, keys)
        library_added = bool(row.get("library_added"))
        has_file = int(row["id"]) in with_library
        if has_file:
            library_added = True
        torrent = torrents.get(int(row["id"]))
        download, progress = _download_state(torrent)
        downloaded = (
            download == "sembrando"
            or int(row["id"]) in downloaded_assets
            or has_file
        )
        publication = (
            "publicado"
            if published
            else "pendiente"
            if downloaded or int(row["id"]) in with_releases
            else "sin_fuente"
        )
        releases, alive = release_counts.get(int(row["id"]), (0, 0))
        available, available_at = _release_available(row.get("air_date"))
        incomplete = bool(
            library_added
            and not downloaded
            and alive == 0
            and available
            and int(row.get("season") or 0) > 0
        )
        return {
            "id": row["id"],
            "key": row["key"],
            "season": int(row["season"] or 0),
            "episode": int(row["episode"] or 0),
            "title": row.get("title"),
            "publication": publication,
            "published": published,
            "library_added": library_added,
            "monitored": bool(row.get("monitored")),
            "available": available,
            "available_at": available_at,
            "download": download,
            "downloaded": downloaded,
            "incomplete": incomplete,
            "playable": bool(published and (downloaded or alive > 0)),
            "progress": progress,
            "releases": releases,
            "alive": alive,
            "in_catalog": True,
            "name": row.get("episode_title"),
            "air_date": None,
            "overview": row.get("episode_overview"),
            "still_path": None,
        }

    states: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        episode = state_for(row)
        states[(episode["season"], episode["episode"])] = episode

    tmdb_seasons = (tmdb_data or {}).get("seasons") or []
    seasons_out: list[dict[str, Any]] = []
    episode_updates: list[tuple[str | None, str | None, int]] = []
    matched: set[tuple[int, int]] = set()
    for season in tmdb_seasons:
        try:
            number = int(season.get("season") or 0)
        except (TypeError, ValueError):
            continue
        episodes: list[dict[str, Any]] = []
        for source in season.get("episodes") or []:
            try:
                episode_number = int(source.get("episode") or 0)
            except (TypeError, ValueError):
                continue
            key = (number, episode_number)
            state = states.get(key)
            if state is not None:
                matched.add(key)
                episode = dict(state)
                new_name = source.get("name")
                new_overview = source.get("overview")
                if (
                    (new_name and new_name != state.get("name"))
                    or (new_overview and new_overview != state.get("overview"))
                ):
                    episode_updates.append(
                        (new_name, new_overview, int(state["id"]))
                    )
            else:
                episode = {
                    "id": None,
                    "key": f"series:tmdb:{tmdb}:{number}:{episode_number}",
                    "season": number,
                    "episode": episode_number,
                    "title": None,
                    "publication": "sin_catalogo",
                    "published": False,
                    "download": "sin_descarga",
                    "downloaded": False,
                    "progress": 0.0,
                    "releases": 0,
                    "alive": 0,
                    "in_catalog": False,
                }
            episode.update(
                {
                    "name": source.get("name") or f"Episodio {episode_number}",
                    "air_date": source.get("air_date"),
                    "overview": source.get("overview"),
                    "still_path": source.get("still_path"),
                    "in_catalog": bool(state),
                }
            )
            available, available_at = _release_available(source.get("air_date"))
            episode["available"] = available
            episode["available_at"] = available_at
            if episode.get("in_catalog"):
                episode["incomplete"] = bool(
                    episode.get("library_added")
                    and not episode.get("downloaded")
                    and int(episode.get("alive") or 0) == 0
                    and available
                    and number > 0
                )
                episode["playable"] = bool(
                    episode.get("published")
                    and (
                        episode.get("downloaded")
                        or int(episode.get("alive") or 0) > 0
                    )
                )
            else:
                episode["incomplete"] = False
                episode["playable"] = False
            episodes.append(episode)
        seasons_out.append(
            {
                "season": number,
                "name": season.get("name") or (
                    "Especiales" if number == 0 else f"Temporada {number}"
                ),
                "air_date": season.get("air_date"),
                "poster_path": season.get("poster_path"),
                "episodes": episodes,
            }
        )

    # Episodios que existen en la base pero no aparecen en TMDB.
    extras: dict[int, list[dict[str, Any]]] = {}
    for key, state in states.items():
        if key in matched:
            continue
        extras.setdefault(state["season"], []).append(state)
    existing_seasons = {season["season"]: season for season in seasons_out}
    for number, episodes in sorted(extras.items()):
        episodes.sort(key=lambda item: item["episode"])
        if number in existing_seasons:
            existing_seasons[number]["episodes"].extend(episodes)
            existing_seasons[number]["episodes"].sort(
                key=lambda item: item["episode"]
            )
        else:
            seasons_out.append(
                {
                    "season": number,
                    "name": "Especiales" if number == 0 else f"Temporada {number}",
                    "air_date": None,
                    "poster_path": None,
                    "episodes": episodes,
                }
            )
    seasons_out.sort(key=lambda season: season["season"])
    for season in seasons_out:
        season["episode_count"] = len(season["episodes"])
        season["catalog_episodes"] = sum(
            1 for episode in season["episodes"] if episode.get("in_catalog")
        )
        season["published"] = sum(
            1 for episode in season["episodes"] if episode.get("published")
        )
        season["library"] = sum(
            1 for episode in season["episodes"] if episode.get("library_added")
        )
        season["monitored"] = sum(
            1 for episode in season["episodes"] if episode.get("monitored")
        )
        season["covered"] = sum(
            1 for episode in season["episodes"]
            if episode.get("in_catalog")
            and (episode.get("library_added") or episode.get("monitored"))
        )
        season["downloaded"] = sum(
            1 for episode in season["episodes"] if episode.get("downloaded")
        )
        season["incomplete"] = sum(
            1 for episode in season["episodes"] if episode.get("incomplete")
        )
        season["aired"] = sum(
            1 for episode in season["episodes"]
            if episode.get("available") is not False
        )
        season["upcoming"] = sum(
            1 for episode in season["episodes"]
            if episode.get("available") is False
        )
        season["catalog_aired"] = sum(
            1 for episode in season["episodes"]
            if episode.get("in_catalog") and episode.get("available") is not False
        )
        season["covered_aired"] = sum(
            1 for episode in season["episodes"]
            if episode.get("in_catalog")
            and episode.get("available") is not False
            and (episode.get("library_added") or episode.get("monitored"))
        )
        season["playable_aired"] = sum(
            1 for episode in season["episodes"]
            if episode.get("available") is not False and episode.get("playable")
        )

    first = rows[0] if rows else {}
    tmdb_series = (tmdb_data or {}).get("series") or {}
    series = {
        "tmdb": tmdb,
        "title": tmdb_series.get("title") or first.get("title"),
        "year": tmdb_series.get("year") or first.get("year"),
        "poster_path": tmdb_series.get("poster_path") or next(
            (row.get("poster_path") for row in rows if row.get("poster_path")),
            None,
        ),
        "backdrop_path": tmdb_series.get("backdrop_path"),
        "overview": tmdb_series.get("overview") or next(
            (row.get("overview") for row in rows if row.get("overview")), None
        ),
        "vote_average": tmdb_series.get("vote_average") if tmdb_series.get("vote_average") is not None else next(
            (row.get("vote_average") for row in rows
             if row.get("vote_average") is not None),
            None,
        ),
        "in_library": bool(lib.get("in_library")),
        "has_file": bool(lib.get("has_file")),
    }
    all_episodes = [
        episode for season in seasons_out for episode in season["episodes"]
    ]
    regular_episodes = [
        episode for episode in all_episodes
        if int(episode.get("season") or 0) != 0
    ]
    special_episodes = [
        episode for episode in all_episodes
        if int(episode.get("season") or 0) == 0
    ]
    totals = {
        "episodes": len(all_episodes),
        "catalog_episodes": len(rows),
        "missing_episodes": sum(
            1 for episode in regular_episodes if not episode.get("in_catalog")
        ),
        "specials": len(special_episodes),
        "published": sum(1 for episode in all_episodes if episode.get("published")),
        "library": sum(1 for episode in all_episodes if episode.get("library_added")),
        "monitored": sum(1 for episode in all_episodes if episode.get("monitored")),
        "monitored_aired": sum(
            1 for episode in regular_episodes
            if episode.get("monitored") and episode.get("available") is not False
        ),
        "covered": sum(
            1 for episode in all_episodes
            if episode.get("in_catalog")
            and (episode.get("library_added") or episode.get("monitored"))
        ),
        "downloaded": sum(
            1 for episode in all_episodes if episode.get("downloaded")
        ),
        "downloading": sum(
            1 for episode in all_episodes
            if episode.get("download") == "descargando"
        ),
        "incomplete": sum(
            1 for episode in all_episodes if episode.get("incomplete")
        ),
        "aired": sum(
            1 for episode in regular_episodes
            if episode.get("available") is not False
        ),
        "upcoming": sum(
            1 for episode in regular_episodes
            if episode.get("available") is False
        ),
        "catalog_aired": sum(
            1 for episode in regular_episodes
            if episode.get("in_catalog") and episode.get("available") is not False
        ),
        "covered_aired": sum(
            1 for episode in regular_episodes
            if episode.get("in_catalog")
            and episode.get("available") is not False
            and (episode.get("library_added") or episode.get("monitored"))
        ),
        "playable_aired": sum(
            1 for episode in regular_episodes
            if episode.get("available") is not False and episode.get("playable")
        ),
        "seasons": len(seasons_out),
    }
    if episode_updates:
        try:
            with closing(connect(db_path)) as conn:
                conn.executemany(
                    "UPDATE media_items SET "
                    "episode_title=COALESCE(?, episode_title), "
                    "episode_overview=COALESCE(?, episode_overview) "
                    "WHERE id=?",
                    episode_updates,
                )
                conn.commit()
        except Exception:
            pass
    from . import lists as lists_mod, naming as naming_mod, profiles as profiles_mod
    s_settings = lists_mod.get_series_settings(db_path, tmdb)
    extra_best = {}
    with closing(connect(db_path)) as _c:
        for br in _c.execute(
            """
            SELECT r.asset_id, r.quality, r.audio, r.source_type, r.tracker, r.is_pack
            FROM releases r
            JOIN media_items m ON m.id = r.asset_id
            WHERE m.kind = 'series' AND m.tmdb = ? AND r.health = 'alive'
            ORDER BY COALESCE(r.size_bytes, 999999999999) ASC
            """,
            (tmdb,),
        ).fetchall():
            aid = int(br["asset_id"])
            if aid not in extra_best:
                extra_best[aid] = dict(br)
    for season in seasons_out:
        for ep in season["episodes"]:
            eid = ep.get("id")
            if eid and int(eid) in extra_best:
                b = extra_best[int(eid)]
                ep["best_quality"] = b.get("quality")
                ep["best_audio"] = b.get("audio")
                ep["best_source_type"] = b.get("source_type")
                ep["best_tracker"] = b.get("tracker")
    return {
        "series": series,
        "totals": totals,
        "seasons": seasons_out,
        "settings": s_settings,
        "profiles": profiles_mod.list_profiles(db_path),
        "root_folders": naming_mod.list_root_folders(db_path),
        "tags": lists_mod.list_tags(db_path),
        "tmdb_ok": bool(tmdb_data),
        "tmdb_errors": (tmdb_data or {}).get("errors") or [],
        "tmdb_url": f"https://www.themoviedb.org/tv/{tmdb}",
    }

def collection_states(db_path, config, parts: list[dict]) -> list[dict]:
    """Cruza las películas de una colección con el catálogo local."""
    inventory = strm_inventory.inventory(config.catalog_library)
    published_keys = inventory["keys"]
    downloaded_ids = _asset_ids_with_downloads(db_path)
    with_releases = _asset_ids_with_releases(db_path)
    tmdb_ids = [int(part["tmdb"]) for part in parts if part.get("tmdb")]
    rows = []
    if tmdb_ids:
        placeholders = ",".join("?" for _ in tmdb_ids)
        with closing(connect(db_path)) as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT id, key, tmdb, title, year, poster_path, monitored, library_added, release_date "
                    f"FROM media_items WHERE kind='movie' AND tmdb IN ({placeholders})",
                    tmdb_ids,
                ).fetchall()
            ]
    by_tmdb = {int(row["tmdb"]): row for row in rows}
    result = []
    for part in parts:
        item = dict(part)
        row = by_tmdb.get(int(part.get("tmdb") or 0))
        if row is None:
            item.update(
                {
                    "asset_id": None,
                    "in_catalog": False,
                    "published": False,
                    "downloaded": False,
                    "publication": "sin_catalogo",
                }
            )
        else:
            published = row["key"] in published_keys
            library_added = bool(row.get("library_added"))
            downloaded = int(row["id"]) in downloaded_ids
            publication = (
                "publicado"
                if library_added
                else "pendiente"
                if downloaded or int(row["id"]) in with_releases
                else "sin_fuente"
            )
            item.update(
                {
                    "asset_id": int(row["id"]),
                    "key": row["key"],
                    "in_catalog": True,
                    "published": published,
                    "library_added": library_added,
                    "downloaded": downloaded,
                    "publication": publication,
                    "monitored": bool(row.get("monitored")),
                    "available": _release_available(row.get("release_date"))[0],
                    "available_at": _release_available(row.get("release_date"))[1],
                    "poster_path": row.get("poster_path") or item.get("poster_path"),
                }
            )
        result.append(item)
    return result


def monitored_items(
    db_path,
    config,
    *,
    q: str = "",
    state: str = "",
    kind: str = "",
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Cola de seguimiento: títulos monitorizados que aún no tienen .strm."""
    inventory = strm_inventory.inventory(config.catalog_library)
    published_keys = inventory["keys"]
    query = (
        "SELECT id, key, kind, tmdb, title, year, season, episode, poster_path, "
        "monitored, monitor_source, monitor_last_search_at, "
        "release_date, air_date "
        "FROM media_items WHERE monitored = 1"
    )
    params: list[Any] = []
    if kind in ("movie", "series"):
        query += " AND kind = ?"
        params.append(kind)
    text = (q or "").strip().lower()
    if text:
        query += " AND (lower(title) LIKE ? OR lower(key) LIKE ?)"
        params.extend([f"%{text}%", f"%{text}%"])
    query += " ORDER BY monitor_last_search_at IS NULL, monitor_last_search_at, id"
    with closing(connect(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        asset_ids = [int(row["id"]) for row in rows]
        release_counts: dict[int, tuple[int, int]] = {}
        active_torrents: dict[int, dict[str, Any]] = {}
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            release_counts = {
                int(row["asset_id"]): (int(row["releases"]), int(row["alive"]))
                for row in conn.execute(
                    f"SELECT asset_id, COUNT(*) AS releases, "
                    f"SUM(CASE WHEN health='alive' THEN 1 ELSE 0 END) AS alive "
                    f"FROM releases WHERE asset_id IN ({placeholders}) "
                    f"GROUP BY asset_id",
                    asset_ids,
                ).fetchall()
            }
            active_torrents = {
                int(row["asset_id"]): dict(row)
                for row in conn.execute(
                    f"SELECT f.asset_id, MIN(t.state) AS state, "
                    f"MAX(t.progress) AS progress "
                    f"FROM torrent_files f JOIN torrents t ON t.id = f.torrent_id "
                    f"WHERE f.asset_id IN ({placeholders}) "
                    f"AND t.category = 'ondemand' "
                    f"AND t.state IN ('waiting', 'downloading') "
                    f"GROUP BY f.asset_id",
                    asset_ids,
                ).fetchall()
            }

    items: list[dict[str, Any]] = []
    for row in rows:
        if row["key"] in published_keys:
            continue
        available, available_at = _release_available(
            row.get("release_date") or row.get("air_date")
        )
        active = active_torrents.get(int(row["id"]))
        last_search = row.get("monitor_last_search_at")
        if active is not None:
            item_state = (
                "downloading" if active.get("state") == "downloading" else "queued"
            )
        elif not available:
            item_state = "pending"
        elif not last_search:
            item_state = "waiting"
        else:
            item_state = "searching"
        releases, alive = release_counts.get(int(row["id"]), (0, 0))
        items.append(
            {
                **row,
                "available": available,
                "available_at": available_at,
                "state": item_state,
                "last_search": last_search,
                "releases": releases,
                "alive": alive,
                "progress": float((active or {}).get("progress") or 0),
            }
        )

    summary = {
        "total": len(items),
        "missing": sum(
            1 for item in items if item["state"] in ("waiting", "searching")
        ),
        "pending": sum(1 for item in items if item["state"] == "pending"),
        "downloading": sum(
            1 for item in items if item["state"] in ("downloading", "queued")
        ),
    }
    if state == "missing":
        items = [item for item in items if item["state"] in ("waiting", "searching")]
    elif state in ("pending", "downloading"):
        wanted = (
            ("downloading", "queued")
            if state == "downloading"
            else (state,)
        )
        items = [item for item in items if item["state"] in wanted]
    per_page = max(1, min(int(per_page or 50), 200))
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * per_page
    return {
        "items": items[start : start + per_page],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "summary": summary,
    }


def pending_targets(
    db_path,
    config,
    *,
    publication: str = "pendiente",
    kind: str = "",
    q: str = "",
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Títulos sin `.strm` que aún se pueden buscar.

    `pendiente` = ya tiene algún release; `sin_fuente` = no tiene ninguno.
    Se devuelven claves reales, porque el buscador trabaja por título.
    """
    publication = publication if publication in ("pendiente", "sin_fuente") else "pendiente"
    inventory = strm_inventory.inventory(config.catalog_library)
    published = inventory["keys"]
    with_releases = _asset_ids_with_releases(db_path)
    text = (q or "").strip().lower()
    with closing(connect(db_path)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, key, kind, tmdb, title, year, season, episode,
                       COALESCE(popularity,0) AS popularity
                FROM media_items
                ORDER BY popularity DESC, title, season, episode
                """
            ).fetchall()
        ]
    targets: list[dict[str, Any]] = []
    for row in rows:
        if row["key"] in published:
            continue
        has_release = int(row["id"]) in with_releases
        if publication == "pendiente" and not has_release:
            continue
        if publication == "sin_fuente" and has_release:
            continue
        if kind in ("movie", "series") and row["kind"] != kind:
            continue
        if text and text not in (row.get("title") or "").lower() \
                and text not in (row.get("key") or "").lower():
            continue
        targets.append(
            {
                "id": int(row["id"]),
                "key": row["key"],
                "kind": row["kind"],
                "tmdb": row.get("tmdb"),
                "title": row.get("title"),
                "year": row.get("year"),
                "season": row.get("season"),
                "episode": row.get("episode"),
                "popularity": row.get("popularity"),
            }
        )
    if limit and limit > 0:
        targets = targets[: int(limit)]
    return targets


def pending_summary(
    db_path,
    config,
    *,
    publication: str = "pendiente",
    kind: str = "",
    q: str = "",
) -> dict[str, Any]:
    targets = pending_targets(
        db_path, config, publication=publication, kind=kind, q=q
    )
    series_tmdb = {
        int(target["tmdb"])
        for target in targets
        if target["kind"] == "series" and target.get("tmdb")
    }
    return {
        "publication": publication,
        "kind": kind,
        "q": q,
        "total": len(targets),
        "movies": sum(1 for target in targets if target["kind"] == "movie"),
        "series_episodes": sum(
            1 for target in targets if target["kind"] == "series"
        ),
        "series": len(series_tmdb),
    }


def search_catalog(db_path, query: str, *, limit: int = 8) -> dict[str, Any]:
    """Búsqueda rápida para la paleta global, sin tocar Radarr/Sonarr."""
    text = (query or "").strip()
    if len(text) < 2:
        return {"items": [], "total": 0}
    pattern = f"%{text.lower()}%"
    prefix = f"{text.lower()}%"
    word = f"% {text.lower()}%"
    with closing(connect(db_path)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, key, kind, tmdb, title, year, season, episode,
                       poster_path, COALESCE(popularity,0) AS popularity
                FROM media_items
                WHERE lower(title) LIKE ? OR lower(original_title) LIKE ?
                   OR lower(key) LIKE ?
                ORDER BY CASE
                    WHEN lower(title) LIKE ? THEN 0
                    WHEN lower(title) LIKE ? THEN 1
                    WHEN lower(original_title) LIKE ? THEN 2
                    ELSE 3
                END, popularity DESC, title
                LIMIT 300
                """,
                (pattern, pattern, pattern, prefix, word, prefix),
            ).fetchall()
        ]
    seen_series: set[int] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "series":
            tmdb = int(row.get("tmdb") or 0)
            if not tmdb or tmdb in seen_series:
                continue
            seen_series.add(tmdb)
            row = {
                **row,
                "key": f"series:tmdb:{tmdb}",
                "season": None,
                "episode": None,
            }
        items.append(row)
        if len(items) >= max(1, min(int(limit), 30)):
            break
    return {"items": items, "total": len(items)}


def item_state(db_path, config, item: dict[str, Any], *, qbit=None, arr=None) -> dict[str, Any]:
    inventory = strm_inventory.inventory(config.catalog_library)
    published = strm_inventory.is_key_published(item, inventory["keys"])
    # Pertenecer a Radarr/Sonarr no es estar en Jellyfin. Son dos estados
    # distintos y la ficha debe poder distinguirlos.
    library_added = bool(item.get("library_added"))
    available, available_at = _release_available(
        item.get("release_date") or item.get("air_date")
    )
    with_releases = _asset_ids_with_releases(db_path)
    # La lista usa el último estado sincronizado de qBittorrent: no bloquea
    # la lectura del catálogo con una llamada de red.
    torrents = _ondemand_torrents(db_path)
    torrent = torrents.get(int(item["id"]))
    dl_state, progress = _download_state(torrent)
    downloaded_assets = _asset_ids_with_downloads(db_path)
    lib = {"in_library": False, "has_file": False}
    if arr is not None and item.get("tmdb"):
        lib = arr.in_library(
            item["kind"],
            int(item["tmdb"]),
            season=item.get("season"),
            episode=item.get("episode"),
        )
    downloaded = (
        dl_state == "sembrando"
        or int(item["id"]) in downloaded_assets
        or bool(lib.get("has_file"))
    )
    if lib.get("has_file"):
        library_added = True
    if published:
        publication = "publicado"
    elif downloaded or int(item["id"]) in with_releases:
        publication = "pendiente"
    else:
        publication = "sin_fuente"
    return {
        "publication": publication,
        "published": published,
        "library_added": library_added,
        "available": available,
        "available_at": available_at,
        "download": dl_state,
        "downloaded": downloaded,
        "progress": progress,
        "in_library": bool(lib.get("in_library")),
        "has_file": bool(lib.get("has_file")),
        "info_hash": (torrent or {}).get("info_hash"),
    }
