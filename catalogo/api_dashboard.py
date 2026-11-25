"""API de portada: resumen, avisos, calendario, actividad y búsqueda."""

from __future__ import annotations

import time
from contextlib import closing
from typing import Any

from . import jobs, ondemand, queries, strm_inventory
from .db import connect
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
    if parts == ["overview"] and not mutate:
        qbit, error = app.qbit()
        return 200, queries.overview(
                db, config=app.config, qbit=qbit, qbit_error=error
            )

    if parts == ["events"] and not mutate:
        limit = _int(params.get("limit"), 100)
        return 200, {"items": queries.list_events(db, limit=limit)}

    if parts == ["calendar"] and not mutate:
        days = _int(params.get("days"), 90)
        items = queries.calendar(
            db,
            days=days,
            start=params.get("start") or None,
            end=params.get("end") or None,
            monitored_only=params.get("monitored") == "1",
            kind=params.get("kind") or "",
        )
        inv = strm_inventory.inventory(app.config.catalog_library)["keys"]
        for it in items:
            it["published"] = it["key"] in inv
        return 200, {
                "days": days,
                "items": items,
            }

    if parts == ["activity"] and not mutate:
        recent = jobs.list_jobs(
            db, limit=30, exclude_kinds=INTERNAL_JOB_KINDS
        )
        active = [
            job for job in recent
            if job.get("state") in ("queued", "running")
        ]
        latest = next(
            (job for job in recent if job.get("state") not in ("queued", "running")),
            None,
        )
        with closing(connect(db)) as conn:
            rows = conn.execute(
                """
                SELECT key, kind, COALESCE(tmdb, id) AS grp
                FROM media_items
                WHERE monitored = 1
                """
            ).fetchall()
        published = strm_inventory.inventory(
            app.config.catalog_library
        )["keys"]
        monitored = sum(1 for r in rows if r["key"] not in published)
        return 200, {
                "active": active,
                "running": next(
                    (job for job in active if job.get("state") == "running"),
                    None,
                ),
                "queued": sum(1 for job in active if job.get("state") == "queued"),
                "latest": latest,
                "monitored": monitored,
                "generated_at": int(time.time()),
            }

    if parts == ["search"] and not mutate:
        return 200, ondemand.search_catalog(
                db,
                params.get("q") or "",
                limit=_int(params.get("limit"), 8),
            )
    return None
