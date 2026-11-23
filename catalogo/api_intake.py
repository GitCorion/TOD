"""Recepción, análisis y alta de listas y torrents."""

from __future__ import annotations

import base64
from typing import Any

from . import admin, intake
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
    client_ip: str = "",
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path

    if parts in (["intake"], ["intake", "reception"]) and not mutate:
        return 200, intake.reception(
                db,
                limit=_int(params.get("limit"), 8),
                include_flows=params.get("flows", "1") != "0",
            )

    if parts == ["intake", "preflight"]:
        if not mutate:
            raise WebError(405, "usa POST")
        try:
            result = intake.preflight(
                app.config,
                str(body.get("text") or ""),
                kind=str(body.get("kind") or "movie"),
            )
        except intake.IntakeError as exc:
            raise WebError(400, str(exc)) from exc
        return 200, result

    if parts == ["intake", "import"]:
        if not mutate:
            raise WebError(405, "usa POST")
        try:
            result = intake.confirm(
                app.config,
                str(body.get("text") or ""),
                kind=str(body.get("kind") or "movie"),
                label=str(body.get("label") or "") or None,
                requested_by=client_ip,
                follow=bool(body.get("follow", True)),
            )
        except intake.IntakeError as exc:
            raise WebError(400, str(exc)) from exc
        app.audit(
            "web.intake_import",
            payload={
                "batch_id": result.get("id"),
                "added": result.get("added"),
                "duplicate": result.get("duplicate"),
                "unknown": result.get("unknown"),
                "errors": result.get("errors"),
            },
        )
        return 200, result

    if parts == ["intake", "torrent"]:
        if not mutate:
            raise WebError(405, "usa POST")
        filename = str(body.get("filename") or "manual.torrent")[:200]
        encoded = str(body.get("data") or "")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise WebError(400, "archivo inválido") from exc
        if len(raw) > intake.MAX_TORRENT_BYTES:
            raise WebError(413, "el .torrent es demasiado grande")
        try:
            result = intake.add_torrent(
                app.config,
                filename=filename,
                raw=raw,
                requested_by=client_ip,
            )
        except intake.IntakeError as exc:
            raise WebError(400, str(exc)) from exc
        app.audit(
            "web.intake_torrent",
            payload={
                "batch_id": result.get("batch_id"),
                "name": result.get("name"),
                "info_hash": result.get("info_hash"),
            },
        )
        return 200, result

    if (
        len(parts) == 3
        and parts[1] == "batches"
        and not mutate
    ):
        batch_id = _int(parts[2], 0)
        if batch_id <= 0:
            raise WebError(400, "lote inválido")
        result = intake.batch_detail(db, batch_id)
        if result is None:
            raise WebError(404, "lote no encontrado")
        return 200, result
    return None
