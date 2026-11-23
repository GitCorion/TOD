"""API de descargas de qBittorrent."""

from __future__ import annotations

import re
from typing import Any

from . import admin, queries
from .errors import WebError

HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def handle(
    app: Any,
    parts: list[str],
    params: dict[str, str],
    body: dict[str, Any],
    *,
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path
    if parts == ["downloads"] and not mutate:
        qbit, error = app.qbit()
        return 200, queries.list_downloads(
                db, qbit, category=params.get("category") or None
            )

    if len(parts) == 3 and parts[0] == "downloads" and parts[2] == "action":
        if not mutate:
            raise WebError(405, "usa POST")
        info_hash = parts[1]
        if not HASH_RE.match(info_hash):
            raise WebError(400, "hash inválido")
        result = admin.qbit_action(
            app.config,
            info_hash,
            str(body.get("action") or ""),
            delete_files=bool(body.get("delete_files")),
        )
        app.audit("web.qbit_action", payload=result)
        return 200, result
    return None
