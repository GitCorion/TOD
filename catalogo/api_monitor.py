"""API de seguimiento, indexers y siembra."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import api_status, ondemand, queries
from .db import connect
from .errors import WebError
from .qbit_write import QbittorrentWriteClient
from .retention import finish_intent


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
    if parts == ["seeds"] and not mutate:
        qbit, _ = app.qbit()
        return 200, queries.list_seeds(
                db,
                limit=_int(params.get("limit"), 100),
                qbit=qbit,
            )

    if len(parts) == 3 and parts[0] == "seeds" and parts[2] == "finish":
        if not mutate:
            raise WebError(405, "usa POST")
        intent_id = _int(parts[1], 0)
        with closing(connect(db)) as conn:
            row = conn.execute(
                """
                SELECT i.info_hash, i.started_at,
                       t.seed_started_at,
                       COALESCE(i.info_hash, t.info_hash) AS effective_hash
                FROM seed_commitments i
                LEFT JOIN torrents t ON t.id = (
                    SELECT id FROM torrents WHERE release_id = i.release_id
                    ORDER BY id DESC LIMIT 1
                )
                WHERE i.id = ?
                """,
                (intent_id,),
            ).fetchone()
        live_state = None
        live_ratio = None
        live_started = None
        if row is not None and row["effective_hash"]:
            qbit, _ = app.qbit()
            if qbit is not None:
                try:
                    torrent = qbit.torrent(str(row["effective_hash"]))
                except Exception:
                    torrent = None
                if torrent is not None:
                    raw = str(torrent.get("state") or "").lower()
                    live_state = "seeding" if any(
                        value in raw
                        for value in ("uploading", "stalledup", "forcedup")
                    ) else "active"
                    live_ratio = float(torrent.get("ratio") or 0)
                    live_started = row["seed_started_at"] or row["started_at"]
        result = finish_intent(
            str(db),
            intent_id,
            state=live_state,
            ratio=live_ratio,
            started=live_started,
        )
        if result.get("closed") and row is not None and row["effective_hash"]:
            try:
                writer = (
                    app.play_service.qbit_write
                    if app.play_service is not None
                    else QbittorrentWriteClient.from_config(app.config)
                )
                writer.set_force_start(str(row["effective_hash"]), False)
            except Exception:
                pass
        app.audit("web.seed_finish", payload=result)
        return 200, result

    if parts == ["indexers"] and not mutate:
        indexers, error = api_status.list_indexers(app.config)
        return 200, {
                "items": indexers,
                "error": error,
                "enabled": sum(
                    1 for item in indexers if item.get("enabled")
                ),
                "total": len(indexers),
            }

    if parts == ["monitor", "health"] and not mutate:
        return 200, api_status.monitor_health(db, app.config)

    if parts == ["monitor"] and not mutate:
        data = ondemand.monitored_items(
            db,
            app.config,
            q=params.get("q") or "",
            state=params.get("state") or "",
            kind=params.get("kind") or "",
            page=_int(params.get("page"), 1),
            per_page=_int(params.get("per_page"), 50),
        )
        data["health"] = api_status.monitor_health(db, app.config)
        return 200, data

    if len(parts) == 3 and parts[0] == "monitor" and parts[1] == "indexers":
        if not mutate:
            raise WebError(405, "usa POST")
        indexer_id = _int(parts[2], 0)
        if indexer_id <= 0:
            raise WebError(400, "indexer inválido")
        enabled = bool(body.get("enabled", True))
        try:
            from .prowlarr import ProwlarrClient

            result = ProwlarrClient.from_config(
                app.config
            ).set_indexer_enabled(indexer_id, enabled)
        except Exception as exc:
            raise WebError(503, "no se pudo cambiar el indexer", str(exc)[:300])
        api_status.invalidate_indexers(app.config)
        app.audit(
            "web.indexer_toggle",
            payload={
                "indexer": indexer_id,
                "enabled": enabled,
                "name": result.get("name"),
            },
        )
        return 200, {
                "id": indexer_id,
                "name": result.get("name"),
                "enabled": bool(result.get("enable", enabled)),
            }
    return None
