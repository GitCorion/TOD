"""API de descargas de qBittorrent."""

from __future__ import annotations

import re
from typing import Any

from . import admin, queries
from .errors import WebError

HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def handle(
    app: Any,
    parts: list[str],
    params: dict[str, str],
    body: dict[str, Any],
    *,
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path

    if parts == ["blocklist"] and not mutate:
        from . import lists as lists_mod
        items = lists_mod.list_blocklist(db, limit=_int(params.get("limit"), 200) if params else 200)
        return 200, {"items": items, "total": len(items)}

    if len(parts) == 3 and parts[0] == "blocklist" and parts[2] == "delete":
        if not mutate:
            raise WebError(405, "usa POST")
        from . import lists as lists_mod, ondemand
        try:
            rid = int(parts[1])
        except ValueError:
            raise WebError(400, "id inválido")
        deleted = lists_mod.unblock_release(db, rid)
        ondemand.invalidate_caches()
        app.audit("web.blocklist_remove", payload={"release_id": rid, "deleted": deleted})
        return 200, {"deleted": deleted, "release_id": rid}


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
