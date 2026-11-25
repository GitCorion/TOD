"""API de sagas y series."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import admin, intake, jellyfin, ondemand, queries, strm_inventory
from . import strm as strm_module
from .db import connect
from .errors import WebError
from .jobs import JobConflict


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
    client_ip: str = "",
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path
    if parts and parts[0] == "collections":
        if len(parts) == 2 and not mutate:
            collection_id = _int(parts[1], 0)
            if collection_id <= 0:
                raise WebError(400, "colección inválida")
            try:
                data = admin.tmdb_collection(app.config, collection_id)
            except Exception as exc:
                raise WebError(404, "colección no encontrada", str(exc)[:200]) from exc
            data["parts"] = ondemand.collection_states(
                db, app.config, data["parts"]
            )
            return 200, data
        if len(parts) == 3 and parts[2] == "add":
            if not mutate:
                raise WebError(405, "usa POST")
            collection_id = _int(parts[1], 0)
            if collection_id <= 0:
                raise WebError(400, "colección inválida")
            action = str(body.get("action") or "catalog")
            if action not in ("catalog", "library"):
                raise WebError(400, "acción no válida")
            try:
                result = admin.import_collection(app.config, collection_id)
            except Exception as exc:
                raise WebError(404, "colección no encontrada", str(exc)[:200]) from exc
            job_id = None
            pending_keys = []
            states = []
            if action == "library":
                states = ondemand.collection_states(
                    db, app.config, result["collection"]["parts"]
                )
                pending_keys = [
                    part["key"]
                    for part in states
                    if part.get("in_catalog")
                    and not part.get("published")
                    and part.get("publication") != "sin_fuente"
                ]
                no_source_keys = [
                    part["key"]
                    for part in states
                    if part.get("in_catalog")
                    and not part.get("published")
                    and part.get("publication") == "sin_fuente"
                ]
                for part in states:
                    if not part.get("in_catalog"):
                        continue
                    admin.set_library(
                        app.config,
                        key=part["key"],
                        member=True,
                        monitored=True
                        if part.get("publication") == "sin_fuente"
                        else None,
                        source="collection",
                    )
                skipped_no_source = len(no_source_keys)
                monitored_count = len(no_source_keys)
                if pending_keys:
                    job_id = app.runner.submit(
                        "publish-now",
                        {
                            "keys": pending_keys,
                            "kind": "movie",
                            "delay": 0.5,
                            "min_seeders": 1,
                        },
                        requested_by=client_ip,
                        allow_duplicate=True,
                    )
            else:
                skipped_no_source = 0
                monitored_count = 0
            app.audit(
                "web.collection_add",
                payload={
                    "collection": collection_id,
                    "action": action,
                    "imported": result["imported"],
                    "keys": len(result["keys"]),
                    "pending": len(pending_keys),
                    "skipped_no_source": skipped_no_source,
                    "monitored": monitored_count,
                    "library": sum(1 for part in states if part.get("in_catalog")),
                    "job_id": job_id,
                },
            )
            return 202, {
                    **result,
                    "pending_keys": pending_keys,
                    "skipped_no_source": skipped_no_source,
                    "monitored": monitored_count,
                    "library": sum(1 for part in states if part.get("in_catalog")),
                    "job_id": job_id,
                }
        raise WebError(404, "no encontrado")

    if parts and parts[0] == "series":

        if len(parts) == 3 and parts[2] == "settings":
            from . import lists as lists_mod
            tmdb = _int(parts[1], 0)
            if tmdb <= 0:
                raise WebError(400, "tmdb inválido")
            if not mutate:
                return 200, lists_mod.get_series_settings(db, tmdb)
            try:
                saved = lists_mod.save_series_settings(db, tmdb, body)
            except ValueError as exc:
                raise WebError(400, str(exc))
            ondemand.invalidate_caches()
            app.audit("web.series_settings", payload={"tmdb": tmdb, "profile": saved.get("quality_profile_id")})
            return 200, saved

        if len(parts) == 3 and parts[2] == "preset":
            if not mutate:
                raise WebError(405, "usa POST")
            from . import lists as lists_mod
            tmdb = _int(parts[1], 0)
            preset = str(body.get("preset") or "")
            try:
                res = lists_mod.apply_series_monitor_preset(db, tmdb, preset)
            except ValueError as exc:
                raise WebError(400, str(exc))
            ondemand.invalidate_caches()
            app.audit("web.series_preset", payload=res)
            return 200, res

        if len(parts) == 5 and parts[2] == "season" and parts[4] == "manual-search":
            if not mutate:
                raise WebError(405, "usa POST")
            tmdb = _int(parts[1], 0)
            season = _int(parts[3], -1)
            if tmdb <= 0 or season < 0:
                raise WebError(400, "serie o temporada inválida")
            with closing(connect(db)) as conn:
                ep_rows = conn.execute(
                    """
                    SELECT id, key, title FROM media_items
                    WHERE kind = 'series' AND tmdb = ? AND season = ?
                    ORDER BY episode ASC
                    """,
                    (tmdb, season),
                ).fetchall()
                series_title = (ep_rows[0]["title"] if ep_rows else "") or f"TMDB {tmdb}"
                anchor = ep_rows[0] if ep_rows else None
                asset_ids = [int(r["id"]) for r in ep_rows]
            custom_q = str(body.get("query") or "").strip() or f"{series_title} S{season:02d}"
            sync_summary = {}
            if anchor is not None:
                try:
                    sync_summary = admin.manual_search(
                        app.config,
                        anchor["key"],
                        min_seeders=_int(body.get("min_seeders"), 0),
                        no_jackett=bool(body.get("no_jackett")),
                        query=custom_q,
                    ).get("summary") or {}
                except Exception as exc:
                    sync_summary = {"errors": [str(exc)[:180]]}
            with closing(connect(db)) as conn:
                releases: list[dict[str, Any]] = []
                seen_ids: set[int] = set()
                for aid in asset_ids:
                    for rel in queries._release_rows(conn, aid):
                        rid = int(rel["id"])
                        if rid in seen_ids:
                            continue
                        seen_ids.add(rid)
                        releases.append(rel)
            releases.sort(key=lambda r: (
                0 if r.get("is_pack") else 1,
                0 if r.get("allowed", True) else 1,
                -(int(r.get("cf_score") or 0)),
                -(int(r.get("seeders") or 0)),
            ))
            return 200, {
                "tmdb": tmdb,
                "season": season,
                "title": series_title,
                "query": custom_q,
                "summary": sync_summary,
                "releases": releases[:200],
            }

        if len(parts) == 3 and parts[2] == "monitor":
            if not mutate:
                raise WebError(405, "usa POST")
            tmdb = _int(parts[1], 0)
            if tmdb <= 0:
                raise WebError(400, "tmdb inválido")
            raw_seasons = body.get("seasons")
            if raw_seasons is None and body.get("season") is not None:
                raw_seasons = [body["season"]]
            if raw_seasons is not None and not isinstance(raw_seasons, list):
                raise WebError(400, "seasons inválido")
            result = admin.set_series_monitored(
                app.config,
                tmdb,
                monitored=bool(body.get("monitored", True)),
                seasons=raw_seasons,
                source="manual",
            )
            app.audit("web.series_monitor", payload=result)
            return 200, result
        if len(parts) == 5 and parts[2] == "season" and parts[4] == "monitor":
            if not mutate:
                raise WebError(405, "usa POST")
            tmdb = _int(parts[1], 0)
            season = _int(parts[3], -1)
            if tmdb <= 0 or season < 0:
                raise WebError(400, "serie o temporada inválida")
            result = admin.set_series_monitored(
                app.config,
                tmdb,
                monitored=bool(body.get("monitored", True)),
                seasons=[season],
                source="manual",
            )
            app.audit("web.season_monitor", payload=result)
            return 200, result
        if len(parts) == 3 and parts[2] == "library":
            if not mutate:
                raise WebError(405, "usa POST")
            tmdb = _int(parts[1], 0)
            if tmdb <= 0:
                raise WebError(400, "tmdb inválido")
            raw_seasons = body.get("seasons") or []
            if not isinstance(raw_seasons, list):
                raise WebError(400, "seasons inválido")
            result = admin.set_library(
                app.config,
                series_tmdb=tmdb,
                seasons=[
                    int(value)
                    for value in raw_seasons
                    if str(value).lstrip("-").isdigit()
                ]
                or None,
                member=bool(body.get("member", True)),
                monitored=body.get("monitored") if "monitored" in body else None,
                source="manual",
            )
            job_id = None
            exported = 0
            jellyfin_scan: dict[str, Any] = {"ok": False, "reason": "sin_rutas"}
            if result.get("member"):
                # Publica ya lo que ya está en Radarr/Sonarr o tiene
                # release; el monitor solo persigue lo que falta.
                for key in result.get("keys", []):
                    try:
                        report = admin.export_strm(
                            app.db_path,
                            app.config.catalog_library,
                            base_url=app.config.playback_base_url,
                            key=key,
                            limit=None,
                            skip_library=False,
                        )
                        exported += int(report.get("written") or 0)
                    except Exception:
                        continue
                strm_inventory.invalidate(app.config.catalog_library)
                keys = result.get("keys", [])
                if keys:
                    with closing(connect(db)) as conn:
                        rows = conn.execute(
                            "SELECT * FROM media_items WHERE key IN ("
                            + ",".join("?" for _ in keys)
                            + ")",
                            keys,
                        ).fetchall()
                    paths = [
                        strm_module.strm_path(
                            app.config.catalog_library, dict(row)
                        )
                        for row in rows
                    ]
                    jellyfin_scan = jellyfin.notify_updated(
                        app.config,
                        [path for path in paths if path.exists()],
                    )
            if result.get("member") and body.get("monitored"):
                published = strm_inventory.inventory(
                    app.config.catalog_library
                )["keys"]
                already = [
                    key for key in result.get("keys", []) if key in published
                ]
                if already:
                    with closing(connect(db)) as conn:
                        conn.execute(
                            "UPDATE media_items SET monitored = 0 "
                            "WHERE key IN ("
                            + ",".join("?" for _ in already)
                            + ")",
                            already,
                        )
                        conn.commit()
                pending = [
                    key
                    for key in result.get("keys", [])
                    if key not in published
                ]
                if pending:
                    try:
                        job_id = app.runner.submit(
                            "monitor",
                            {
                                "keys": pending,
                                "limit": len(pending),
                                "delay": 1,
                            },
                            requested_by=client_ip,
                        )
                    except JobConflict:
                        job_id = None
            app.audit(
                "web.series_library",
                payload={
                    **result,
                    "job_id": job_id,
                    "exported": exported,
                    "jellyfin_scan": jellyfin_scan,
                },
            )
            return 200, {
                    **result,
                    "job_id": job_id,
                    "exported": exported,
                    "jellyfin_scan": jellyfin_scan,
                }
        if len(parts) == 3 and parts[2] == "unpublish":
            if not mutate:
                raise WebError(405, "usa POST")
            tmdb = _int(parts[1], 0)
            if tmdb <= 0:
                raise WebError(400, "tmdb inválido")
            result = admin.remove_series_from_library(app.config, tmdb)
            app.audit("web.series_unpublish", payload=result)
            return 200, result
        if not mutate and len(parts) == 2:
            tmdb = _int(parts[1], 0)
            if tmdb <= 0:
                raise WebError(400, "tmdb inválido")
            qbit, _ = app.qbit()
            tmdb_data = None
            tmdb_error = None
            try:
                tmdb_data = admin.tmdb_series_full(app.config, tmdb)
            except Exception as exc:
                tmdb_error = str(exc)[:200]
            detail = ondemand.series_detail(
                db,
                app.config,
                tmdb,
                qbit=qbit,
                arr=app.arr,
                tmdb_data=tmdb_data,
            )
            if detail is None:
                raise WebError(404, "serie no encontrada")
            detail["tmdb_error"] = tmdb_error
            if not detail["series"].get("poster_path"):
                detail["series"]["poster_path"] = admin.tmdb_poster(
                    app.config, "series", tmdb
                )
            return 200, detail
        raise WebError(404, "no encontrado")
    return None
