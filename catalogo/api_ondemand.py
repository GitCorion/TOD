"""API de catálogo bajo demanda.

El transporte solo aporta `app`, parámetros y cuerpo; aquí se decide el
estado, se lanzan tareas y se devuelve `(status, payload)`.
"""

from __future__ import annotations

import time
from contextlib import closing
from typing import Any

from . import admin, jobs, ondemand, settings
from .db import connect
from .errors import WebError


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

    if parts == ["ondemand", "overview"] and not mutate:
        qbit, _ = app.qbit()
        return 200, ondemand.overview(
                db, app.config, qbit=qbit, arr=app.arr
            )

    if parts == ["ondemand", "recent"] and not mutate:
        return 200, ondemand.recent_published(
                db, app.config, limit=_int(params.get("limit"), 6)
            )

    if parts == ["ondemand", "catalog"] and not mutate:
        return 200, ondemand.catalog(
                db,
                app.config,
                q=params.get("q") or "",
                kind=params.get("kind") or "",
                publication=params.get("publication") or "",
                download=params.get("download") or "",
                library=params.get("library") or "",
                origin=params.get("origin") or "",
                incomplete=params.get("incomplete") == "1",
                profile_id=_int(params.get("profile_id"), 0) or None,
                quality=params.get("quality") or "",
                audio=params.get("audio") or "",
                monitored=params.get("monitored") or "",
                tracker=params.get("tracker") or "",
                privacy=params.get("privacy") or "",
                tag=params.get("tag") or "",
                genre=params.get("genre") or "",
                series_status=params.get("series_status") or "",
                root_folder=params.get("root_folder") or "",
                year_min=_int(params.get("year_min"), 0) or None,
                year_max=_int(params.get("year_max"), 0) or None,
                cutoff_unmet=params.get("cutoff_unmet") == "1",
                custom_filter_id=_int(params.get("custom_filter_id"), 0) or None,
                page=_int(params.get("page"), 1),
                per_page=_int(params.get("per_page"), 25),
                group=params.get("group") or "episode",
                sort=params.get("sort") or "popularity",
                facets=params.get("facets") != "0",
            )

    if parts == ["ondemand", "pending-summary"] and not mutate:
        publication = params.get("publication") or "pendiente"
        if publication not in ("pendiente", "sin_fuente"):
            raise WebError(400, "estado de Jellyfin no válido")
        return 200, ondemand.pending_summary(
                db,
                app.config,
                publication=publication,
                kind=params.get("kind") or "",
                q=params.get("q") or "",
            )

    if parts == ["ondemand", "sync-pending"]:
        if not mutate:
            raise WebError(405, "usa POST")
        publication = str(body.get("publication") or "pendiente")
        if publication not in ("pendiente", "sin_fuente"):
            raise WebError(400, "estado de Jellyfin no válido")
        kind = str(body.get("kind") or "")
        if kind not in ("", "movie", "series"):
            raise WebError(400, "tipo no válido")
        params = {
            "publication": publication,
            "kind": kind,
            "q": str(body.get("q") or "")[:200],
            "limit": max(0, _int(body.get("limit"), 0)),
            "delay": max(0.0, float(body.get("delay") if body.get("delay") is not None else 1.0)),
            "min_seeders": max(0, _int(body.get("min_seeders") if body.get("min_seeders") is not None else 1, 1)),
            "no_jackett": bool(body.get("no_jackett")),
        }
        job_id = app.runner.submit(
            "sync-pending", params, requested_by=client_ip
        )
        app.audit(
            "web.sync_pending",
            payload={"job_id": job_id, **params},
        )
        return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}

    if parts == ["ondemand", "settings"]:
        if mutate:
            try:
                value = settings.set_ondemand_max_active(
                    db, _int(body.get("max_active"), 0)
                )
            except ValueError as exc:
                raise WebError(400, str(exc))
            app.audit(
                "web.ondemand_limit", payload={"max_active": value}
            )
            return 200, {
                    "max_active": value,
                    "min": settings.MIN_MAX_ACTIVE,
                    "max": settings.MAX_MAX_ACTIVE,
                }
        return 200, {
                "max_active": settings.ondemand_max_active(
                    db, getattr(app.config, "max_active_ondemand", 4)
                ),
                "min": settings.MIN_MAX_ACTIVE,
                "max": settings.MAX_MAX_ACTIVE,
            }

    if parts == ["ondemand", "publish"]:
        if not mutate:
            raise WebError(405, "usa POST")
        keys = body.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            raise WebError(400, "faltan keys")
        keys = [str(key)[:200] for key in keys][:1000]
        job_id = app.runner.submit(
            "export-strm",
            {"keys": keys},
            requested_by=client_ip,
        )
        app.audit(
            "web.ondemand_publish", payload={"job_id": job_id, "keys": len(keys)}
        )
        return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}

    if parts == ["ondemand", "acquire"]:
        if not mutate:
            raise WebError(405, "usa POST")
        action = str(body.get("action") or "").strip()
        if action not in ("publish", "download"):
            raise WebError(400, "acción no válida")
        keys = body.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            raise WebError(400, "faltan keys")
        keys = [str(key)[:200] for key in keys][:1000]
        kind = "series" if body.get("kind") == "series" else "movie"
        if action == "publish":
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with closing(connect(db)) as conn:
                conn.execute(
                    "UPDATE media_items SET library_added = 1, "
                    "library_added_at = COALESCE(library_added_at, ?), "
                    "library_source = COALESCE(library_source, 'bulk') "
                    "WHERE key IN ("
                    + ",".join("?" for _ in keys)
                    + ")",
                    [stamp, *keys],
                )
                conn.commit()
        job_kind = "publish-now" if action == "publish" else "download-now"
        job_id = app.runner.submit(
            job_kind,
            {
                "keys": keys,
                "kind": kind,
                "delay": 0.5,
                "min_seeders": 1,
            },
            requested_by=client_ip,
            allow_duplicate=True,
        )
        app.audit(
            "web.ondemand_acquire",
            payload={
                "job_id": job_id,
                "action": action,
                "keys": len(keys),
            },
        )
        return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}

    if parts == ["ondemand", "download"]:
        if not mutate:
            raise WebError(405, "usa POST")
        key = str(body.get("key") or "").strip()
        if not key:
            raise WebError(400, "falta key")
        release_id = _int(body.get("release_id"), 0) or None
        try:
            result = admin.download_now(
                app.config,
                app.play_service,
                key,
                release_id=release_id,
                client_ip=client_ip,
                force=True,
            )
        except Exception as exc:
            detail = getattr(exc, "decision", None) or str(exc)[:300]
            raise WebError(503, "no se pudo descargar", detail)
        app.audit(
            "web.ondemand_download",
            payload={
                "key": key,
                "release_id": result.get("release_id"),
                "info_hash": result.get("info_hash"),
                "forced": True,
            },
        )
        return 202, result

    if parts == ["ondemand", "mass-edit"]:
        if not mutate:
            raise WebError(405, "usa POST")
        from . import jellyfin, lists as lists_mod, strm_inventory
        keys = body.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise WebError(400, "faltan keys")
        try:
            kwargs: dict[str, Any] = {
                "monitored": body["monitored"] if "monitored" in body and body["monitored"] is not None else None,
                "monitor_mode": body.get("monitor_mode") or None,
                "add_tag_ids": body.get("add_tag_ids") or [],
                "remove_tag_ids": body.get("remove_tag_ids") or [],
                "out_dir": app.config.catalog_library,
                "reorganize": bool(body.get("reorganize")),
            }
            if "quality_profile_id" in body:
                val = body.get("quality_profile_id")
                kwargs["quality_profile_id"] = int(val) if val not in (None, "", 0, "0") else None
            if "root_folder_id" in body:
                val = body.get("root_folder_id")
                kwargs["root_folder_id"] = int(val) if val not in (None, "", 0, "0") else None
            res = lists_mod.mass_edit_media(db, keys, **kwargs)
        except ValueError as exc:
            raise WebError(400, str(exc))
        ondemand.invalidate_caches()
        reorg = res.get("reorganized") or {}
        if reorg.get("moved"):
            strm_inventory.invalidate(app.config.catalog_library)
            jellyfin.notify_updated(app.config, (reorg.get("affected_paths") or [])[:500])
        app.audit("web.mass_edit", payload={"keys": len(keys), "movies": res["movies"], "series": res["series"]})
        return 200, res

    if parts == ["ondemand", "cutoff-unmet"] and not mutate:
        from . import lists as lists_mod
        return 200, lists_mod.list_cutoff_unmet(
            db,
            limit=_int(params.get("limit"), 200),
            kind=params.get("kind") or "",
        )

    if parts == ["ondemand", "upgrade-cutoff"]:
        if not mutate:
            raise WebError(405, "usa POST")
        from . import lists as lists_mod
        keys = body.get("keys") or None
        res = lists_mod.upgrade_cutoff_pointers(db, keys=keys)
        ondemand.invalidate_caches()
        app.audit("web.upgrade_cutoff", payload={"upgraded": res.get("upgraded", 0)})
        return 200, res

    return None
