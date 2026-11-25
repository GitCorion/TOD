"""API de títulos: alta, ficha, biblioteca, seguimiento y trabajos."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import admin, intake, jellyfin, jobs, ondemand, queries
from .db import connect
from .errors import WebError
from .jobs import JobConflict
from .status import asset_status
from . import strm as strm_module


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle(
    app: Any,
    parts: list[str],
    params: dict[str, str],
    body: dict[str, Any],
    *,
    client_ip: str,
    mutate: bool,
) -> tuple[int, Any] | None:
    db = app.db_path
    db = app.db_path

    if len(parts) == 1:
        if mutate:
            kind = str(body.get("kind") or "movie")
            tmdb = _int(body.get("tmdb"), 0)
            if not tmdb:
                raise WebError(400, "falta tmdb")
            origin = str(body.get("origin") or "tmdb")[:40]
            follow = bool(body.get("follow", True))
            if kind == "series":
                seasons = body.get("seasons") or []
                if not isinstance(seasons, list) or not seasons:
                    raise WebError(400, "elige al menos una temporada")
                seasons = [_int(value, 0) for value in seasons]
                seasons = [value for value in seasons if value > 0]
                if not seasons:
                    raise WebError(400, "temporadas inválidas")
                result = admin.add_series(
                    app.config, tmdb, seasons, origin=origin
                )
                intake.set_follow(db, result.get("keys") or [], follow)
                app.audit("web.add_series", payload={
                    "tmdb": tmdb, "seasons": seasons,
                    "episodes": result.get("episodes"),
                    "origin": origin, "follow": follow,
                })
            else:
                result = admin.add_movie(
                    app.config, tmdb, origin=origin
                )
                intake.set_follow(db, [result.get("key")], follow)
                app.audit("web.add_movie", payload={
                    "tmdb": tmdb, "key": result.get("key"),
                    "origin": origin, "follow": follow,
                })
            return 200, result
        return 200, queries.list_titles(
                db,
                q=params.get("q") or "",
                kind=params.get("kind") or "",
                status=params.get("status") or "",
                availability=params.get("availability") or "",
                page=_int(params.get("page"), 1),
                per_page=_int(params.get("per_page"), 25),
                sort=params.get("sort") or "popularity",
            )

    asset_id = _int(parts[1], 0)
    if asset_id <= 0:
        raise WebError(400, "id inválido")
    if len(parts) == 2 and not mutate:
        qbit, _ = app.qbit()
        detail = queries.title_detail(db, asset_id, qbit=qbit)
        if detail is None:
            raise WebError(404, "título no encontrado")
        item = detail["item"]
        if item.get("tmdb"):
            detail["poster_path"] = item.get("poster_path") or admin.tmdb_poster(
                app.config,
                item["kind"],
                int(item["tmdb"]),
            )
        detail["ondemand"] = ondemand.item_state(
            db, app.config, detail["item"], qbit=qbit, arr=app.arr
        )
        if detail["item"].get("kind") == "movie" and detail["item"].get("tmdb"):
            try:
                detail["collection"] = admin.tmdb_movie_collection(
                    app.config, int(detail["item"]["tmdb"])
                )
            except Exception:
                detail["collection"] = None
        from . import lists as lists_mod, naming as naming_mod, profiles as profiles_mod
        detail["profiles"] = profiles_mod.list_profiles(db)
        detail["root_folders"] = naming_mod.list_root_folders(db)
        detail["all_tags"] = lists_mod.list_tags(db)
        if item.get("tmdb"):
            detail["tags"] = lists_mod.get_media_tags(db, item["kind"], int(item["tmdb"]))
        else:
            detail["tags"] = []
        return 200, detail

    if len(parts) == 3 and parts[2] == "status" and not mutate:
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT key FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        qbit, _ = app.qbit()
        return 200, asset_status(str(db), row["key"], qbit=qbit) or {}

    if len(parts) == 3 and parts[2] == "library":
        if not mutate:
            raise WebError(405, "usa POST")
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT * FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        result = admin.set_library(
            app.config,
            key=row["key"],
            member=bool(body.get("member", True)),
            monitored=body.get("monitored") if "monitored" in body else None,
            source="manual",
        )
        jellyfin_scan: dict[str, Any] = {"ok": False, "reason": "sin_rutas"}
        if result.get("member"):
            path = strm_module.strm_path(
                app.config.catalog_library, dict(row)
            )
            if path.exists():
                jellyfin_scan = jellyfin.notify_updated(
                    app.config, [path]
                )
        app.audit(
            "web.title_library",
            asset_id=asset_id,
            payload={**result, "jellyfin_scan": jellyfin_scan},
        )
        return 200, {**result, "jellyfin_scan": jellyfin_scan}

    if len(parts) == 3 and parts[2] == "monitor":
        if not mutate:
            raise WebError(405, "usa POST")
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT key FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        monitored = bool(body.get("monitored", True))
        result = admin.set_monitored(
            app.config, row["key"], monitored=monitored, source="manual"
        )
        job_id = None
        if monitored:
            try:
                job_id = app.runner.submit(
                    "monitor",
                    {"keys": [row["key"]], "limit": 1, "delay": 0},
                    requested_by=client_ip,
                )
            except JobConflict:
                job_id = None
        app.audit(
            "web.title_monitor",
            asset_id=asset_id,
            payload={"key": row["key"], "monitored": monitored, "job_id": job_id},
        )
        return 200, {**result, "job_id": job_id}

    if len(parts) == 3 and parts[2] == "unpublish":
        if not mutate:
            raise WebError(405, "usa POST")
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT key FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        result = admin.remove_from_library(app.config, row["key"])
        app.audit(
            "web.library_remove",
            asset_id=asset_id,
            payload={"key": row["key"], "removed": result["removed"]},
        )
        return 200, result

    if len(parts) == 3 and parts[2] == "manual-search":
        if not mutate:
            raise WebError(405, "usa POST")
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT key FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        try:
            result = admin.manual_search(
                app.config,
                row["key"],
                min_seeders=_int(body.get("min_seeders"), 0),
                no_jackett=bool(body.get("no_jackett")),
                query=str(body.get("query") or "").strip() or None,
            )
        except Exception as exc:
            raise WebError(400, "no se pudo buscar", str(exc)[:300])
        app.audit(
            "web.manual_search",
            asset_id=asset_id,
            payload={
                "key": row["key"],
                "releases": len(result.get("releases") or []),
            },
        )
        return 200, result

    if len(parts) == 3 and parts[2] in ("sync", "export"):
        if not mutate:
            raise WebError(405, "usa POST")
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT id, key, kind FROM media_items WHERE id=?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise WebError(404, "título no encontrado")
        keys = [row["key"]]
        if isinstance(body.get("keys"), list):
            keys = [str(key)[:200] for key in body["keys"]][:200]
        kind = "series" if row["kind"] == "series" else "movie"
        if parts[2] == "sync":
            job_kind = "sync-sources"
            job_params = {
                "keys": keys,
                "kind": kind,
                "delay": float(body.get("delay") or 0.5),
                "min_seeders": _int(body.get("min_seeders"), 1),
                "no_jackett": bool(body.get("no_jackett")),
            }
        else:
            job_kind = "export-strm"
            job_params = {
                "keys": keys,
                "min_seeders": _int(body.get("min_seeders"), 1),
                "versions": body.get("versions") or "available",
                "require_health": bool(body.get("require_health")),
            }
        job_id = app.runner.submit(
            job_kind, job_params, requested_by=client_ip
        )
        app.audit(
            "web.title_" + parts[2],
            asset_id=asset_id,
            payload={"job_id": job_id, "keys": len(keys)},
        )
        return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}

    if len(parts) == 3 and parts[2] == "settings":
        if not mutate:
            raise WebError(405, "usa POST")
        from . import lists as lists_mod
        with closing(connect(db)) as conn:
            row = conn.execute(
                "SELECT id, key, kind, tmdb FROM media_items WHERE id=?", (asset_id,)
            ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            qp_id = (
                int(body["quality_profile_id"])
                if body.get("quality_profile_id") not in (None, "", 0, "0")
                else None
            )
            rf_id = (
                int(body["root_folder_id"])
                if body.get("root_folder_id") not in (None, "", 0, "0")
                else None
            )
            min_avail = str(body.get("minimum_availability") or "released")
            if min_avail not in ("announced", "in_cinemas", "released"):
                min_avail = "released"
            conn.execute(
                """
                UPDATE media_items SET
                    quality_profile_id = ?,
                    root_folder_id = ?,
                    minimum_availability = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (qp_id, rf_id, min_avail, asset_id),
            )
            conn.commit()
        if "tag_ids" in body and row["tmdb"] and isinstance(body["tag_ids"], list):
            lists_mod.set_media_tags(db, row["kind"], int(row["tmdb"]), body["tag_ids"])
        ondemand.invalidate_caches()
        return 200, {
            "id": asset_id,
            "quality_profile_id": qp_id,
            "root_folder_id": rf_id,
            "minimum_availability": min_avail,
        }

    return None
