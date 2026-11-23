"""Configuración, tareas programadas, avisos y sistema."""

from __future__ import annotations

from typing import Any

from . import admin, api_status, app_settings, attention, integrations, jellyfin, settings
from .errors import WebError
from .jobs import JobConflict
from .scheduler import list_tasks, save_task


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
    if parts == ["settings"]:
        if not mutate:
            return 200, {
                    **app_settings.serialize(app.config),
                    "paths": {
                        setting.key: app_settings.path_status(
                            getattr(app.config, setting.attr)
                        )
                        for setting in app_settings.SETTINGS
                        if setting.kind == "path"
                        and getattr(app.config, setting.attr, None)
                        and not (
                            setting.key == "library_root"
                            and (
                                app.config.library_root_movies
                                or app.config.library_root_series
                            )
                        )
                    },
                }
        raw_values = body.get("values")
        if raw_values is None:
            raw_values = {
                key: value
                for key, value in body.items()
                if key in app_settings.BY_KEY
            }
        if not isinstance(raw_values, dict):
            raise WebError(400, "values debe ser un objeto")
        clear = body.get("clear") or []
        if not isinstance(clear, list):
            raise WebError(400, "clear debe ser una lista")
        try:
            result = app_settings.save_settings(
                app.config.db_path, raw_values, clear=clear
            )
        except ValueError as exc:
            raise WebError(400, str(exc)) from exc
        config = app.reload_config()
        try:
            app.runner.submit("attention-check", requested_by="settings")
        except JobConflict:
            pass
        app.audit(
            "web.settings_save",
            payload={
                "saved": result["saved"],
                "cleared": result["cleared"],
            },
        )
        return 200, {
                **result,
                **app_settings.serialize(config),
                "restart_labels": [
                    app_settings.BY_KEY[key].label
                    for key in result.get("restart_required", [])
                    if key in app_settings.BY_KEY
                ],
            }

    if parts == ["settings", "path-check"]:
        if not mutate:
            raise WebError(405, "usa POST")
        key = str(body.get("key") or "")
        raw_path = body.get("path")
        if not raw_path and key:
            setting = app_settings.BY_KEY.get(key)
            if setting is None:
                raise WebError(400, "ajuste desconocido")
            raw_path = getattr(app.config, setting.attr, None)
        if not raw_path:
            raise WebError(400, "falta path")
        return 200, app_settings.path_status(str(raw_path))

    if (
        len(parts) == 3
        and parts[0] == "integrations"
        and parts[2] == "test"
    ):
        if not mutate:
            raise WebError(405, "usa POST")
        values = body.get("values") or {}
        if not isinstance(values, dict):
            raise WebError(400, "values debe ser un objeto")
        try:
            config = app_settings.preview_config(app.config, values)
            result = integrations.test_integration(parts[1], config)
        except (ValueError, integrations.IntegrationError) as exc:
            raise WebError(400, str(exc)) from exc
        attention.record(app.db_path, parts[1], result)
        app.audit(
            "web.integration_test",
            payload={
                "name": parts[1],
                "ok": result.get("ok"),
                "latency_ms": result.get("latency_ms"),
            },
        )
        return 200, result

    if parts == ["scheduler"] and not mutate:
        return 200, {"items": list_tasks(app.db_path)}

    if len(parts) == 2 and parts[0] == "scheduler":
        if not mutate:
            raise WebError(405, "usa POST")
        try:
            state = save_task(app.db_path, parts[1], body)
        except ValueError as exc:
            raise WebError(400, str(exc)) from exc
        app.audit(
            "web.scheduler_save",
            payload={"task": parts[1], **body},
        )
        item = next(
            (
                task
                for task in list_tasks(app.db_path)
                if task["name"] == parts[1]
            ),
            None,
        )
        return 200, item or state

    if (
        len(parts) == 3
        and parts[0] == "scheduler"
        and parts[2] == "run"
    ):
        if not mutate:
            raise WebError(405, "usa POST")
        try:
            result = app.scheduler.run_now(parts[1])
        except ValueError as exc:
            raise WebError(400, str(exc)) from exc
        except JobConflict as exc:
            raise WebError(409, str(exc)) from exc
        app.audit("web.scheduler_run", payload=result)
        return 202, result

    if parts == ["attention"]:
        if mutate:
            raise WebError(405, "usa GET")
        return 200, attention.status(db, app.config)

    if parts == ["attention", "check"]:
        if not mutate:
            raise WebError(405, "usa POST")
        job_id = app.runner.submit("attention-check", requested_by="web")
        app.audit("web.attention_check", payload={"job_id": job_id})
        return 202, {"job_id": job_id}

    if parts == ["setup"] and not mutate:
        config = app.config
        capabilities = {
            "qbittorrent": bool(config.qbittorrent_pass),
            "prowlarr": bool(config.prowlarr_api_key),
            "jackett": bool(config.jackett_api_key),
            "jellyfin": bool(config.jellyfin_api_key),
            "radarr": bool(config.radarr_api_key),
            "sonarr": bool(config.sonarr_api_key),
            "tmdb": bool(config.tmdb_api_key),
        }
        paths = {
            "catalog_library": app_settings.path_status(
                config.catalog_library
            ),
            "downloads_root": app_settings.path_status(
                config.downloads_root
            ),
        }
        missing = [
            name for name, ok in capabilities.items()
            if not ok and name in ("qbittorrent", "jellyfin", "tmdb")
        ]
        if not (capabilities["prowlarr"] or capabilities["jackett"]):
            missing.append("prowlarr")
        if not paths["catalog_library"].get("exists"):
            missing.append("catalog_library")
        return 200, {
                "capabilities": capabilities,
                "missing": missing,
                "paths": paths,
            }

    if parts == ["system"] and not mutate:
        qbit, qbit_error = app.qbit()
        return 200, api_status.system_payload(
                config=app.config,
                db_path=app.db_path,
                qbit=qbit,
                qbit_error=qbit_error,
                play_service=app.play_service,
                play_error=app.play_error,
                token=app.token,
            )

    if parts == ["system", "qbit-limits"]:
        if not mutate:
            raise WebError(405, "usa POST")
        result = admin.apply_qbit_limits(
            app.config,
            radarr_kbps=_int(body.get("radarr_kbps"), admin.DEFAULT_RADARR_KBPS),
            sonarr_kbps=_int(body.get("sonarr_kbps"), admin.DEFAULT_SONARR_KBPS),
            ondemand_kbps=_int(body.get("ondemand_kbps"), admin.DEFAULT_ONDEMAND_KBPS),
            dry_run=bool(body.get("dry_run")),
        )
        app.audit("web.qbit_limits", payload=result)
        return 200, result

    if parts == ["trackers", "block"]:
        if not mutate:
            raise WebError(405, "usa POST")
        result = admin.block_tracker(
            app.config,
            str(body.get("tracker") or ""),
            blocked=bool(body.get("blocked", True)),
            reason=str(body.get("reason") or "downloads_broken"),
            dry_run=bool(body.get("dry_run")),
            delete_strm=bool(body.get("delete_strm")),
        )
        app.audit("web.tracker_block", payload=result)
        return 200, result
    return None
