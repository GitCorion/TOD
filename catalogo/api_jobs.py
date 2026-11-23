"""API de tareas: listar, consultar, lanzar y cancelar."""

from __future__ import annotations

from typing import Any

from . import admin, jobs
from .errors import WebError

INTERNAL_JOB_KINDS = ("attention-check",)


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
    if parts == ["jobs"]:
        if mutate:
            kind = str(body.get("kind") or "").strip()
            if kind not in admin.JOB_HANDLERS:
                raise WebError(400, "tipo de tarea no permitido", {"kind": kind})
            raw_params = body.get("params") or {}
            if not isinstance(raw_params, dict):
                raise WebError(400, "params debe ser un objeto")
            keys = raw_params.get("keys")
            if keys is not None:
                if not isinstance(keys, list) or len(keys) > 200:
                    raise WebError(400, "keys inválidas")
                raw_params["keys"] = [str(key)[:200] for key in keys]
            job_id = app.runner.submit(
                kind,
                raw_params,
                requested_by=client_ip,
            )
            app.audit("web.job", payload={"kind": kind, "job_id": job_id, "params": raw_params})
            return 202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}
        state = params.get("state") or None
        return 200, {
                "items": jobs.list_jobs(
                    db,
                    limit=_int(params.get("limit"), 50),
                    state=state,
                    exclude_kinds=INTERNAL_JOB_KINDS,
                )
            }

    if len(parts) == 2 and parts[0] == "jobs":
        job_id = _int(parts[1], 0)
        job = jobs.get_job(db, job_id)
        if job is None:
            raise WebError(404, "tarea no encontrada")
        return 200, job

    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
        if not mutate:
            raise WebError(405, "usa POST")
        job = app.runner.cancel(_int(parts[1], 0))
        if job is None:
            raise WebError(404, "tarea no encontrada")
        app.audit("web.job_cancel", payload={"job_id": job["id"]})
        return 200, job
    return None
