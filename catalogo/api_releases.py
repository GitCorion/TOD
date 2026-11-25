"""API de versiones: fijar, verificar y preparar releases."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import admin, jellyfin, jobs, ondemand, strm_inventory
from .db import connect
from .errors import WebError



def _publish_key_strm(app: Any, key: str | None) -> dict[str, Any]:
    """Exporta `.strm` para un título concreto y notifica a Jellyfin si hubo cambios."""
    if not key:
        return {"written": 0, "paths": []}
    try:
        radarr, sonarr = admin._arr_clients(app.config)
        published = admin.export_strm(
            app.config.db_path,
            app.config.catalog_library,
            base_url=app.config.playback_base_url,
            key=key,
            limit=None,
            min_seeders=1,
            versions_filter="available",
            radarr=radarr,
            sonarr=sonarr,
        )
        paths = published.get("written_paths") or []
        if paths:
            strm_inventory.invalidate(app.config.catalog_library)
            jellyfin.notify_updated(app.config, paths[:100])
        return {
            "written": published.get("written", 0),
            "paths": paths[:10],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle(
    app: Any,
    parts: list[str],
    body: dict[str, Any],
    *,
    client_ip: str = "",
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path
    db = app.db_path
    release_id = _int(parts[1], 0) if len(parts) > 1 else 0
    if release_id <= 0:
        raise WebError(400, "release inválido")
    with closing(connect(db)) as conn:
        row = conn.execute(
            """
            SELECT r.id, r.asset_id, m.key, m.title
            FROM releases r JOIN media_items m ON m.id = r.asset_id
            WHERE r.id = ?
            """,
            (release_id,),
        ).fetchone()
    if row is None:
        raise WebError(404, "release no encontrado")

    if len(parts) == 3 and parts[2] == "target":
        if not mutate:
            raise WebError(405, "usa POST")
        source_kind = str(body.get("source_kind") or "auto")
        result = admin.set_target(
            app.config,
            row["key"],
            source_kind,
            release_id=release_id,
            file_id=_int(body.get("file_id"), 0) or None,
            note=str(body.get("note") or "")[:300] or None,
        )
        if source_kind in ("torrent", "auto"):
            result["strm"] = _publish_key_strm(app, result.get("key") or row["key"])
        ondemand.invalidate_caches()
        app.audit(
            "web.set_target", asset_id=row["asset_id"], payload=result
        )
        return 200, result

    if len(parts) == 3 and parts[2] == "health":
        if not mutate:
            raise WebError(405, "usa POST")
        job_id = app.runner.submit(
            "health",
            {
                "key": row["key"],
                "limit": 1,
                "delay": 0,
                "dry_run": bool(body.get("dry_run")),
            },
            requested_by=client_ip,
        )
        app.audit(
            "web.release_health",
            asset_id=row["asset_id"],
            payload={"job_id": job_id, "release_id": release_id},
        )
        return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}

    if len(parts) == 3 and parts[2] == "prepare":
        if not mutate:
            raise WebError(405, "usa POST")
        try:
            result = admin.prepare_release(
                app.config,
                app.play_service,
                release_id,
                client_ip=client_ip,
            )
        except Exception as exc:
            detail = getattr(exc, "decision", None) or str(exc)[:300]
            raise WebError(503, "no se pudo preparar", detail)
        result["strm"] = _publish_key_strm(app, result.get("key") or row["key"])
        ondemand.invalidate_caches()
        app.audit(
            "web.prepare", asset_id=row["asset_id"], payload={
                "release_id": release_id,
                "info_hash": result.get("info_hash"),
            },
        )
        return 202, result

    if len(parts) == 3 and parts[2] == "blocklist":
        if not mutate:
            raise WebError(405, "usa POST")
        from . import lists as lists_mod, ondemand
        reason = str(body.get("reason") or "manual")
        days = _int(body.get("days"), 3650)
        try:
            res = lists_mod.block_release(db, release_id, reason=reason, days=days)
        except ValueError as exc:
            raise WebError(404, str(exc))
        ondemand.invalidate_caches()
        app.audit("web.release_blocklist", asset_id=row["asset_id"], payload=res)
        return 200, res

    return None
