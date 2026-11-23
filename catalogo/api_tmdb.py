"""API de TMDB: búsqueda, temporadas y episodios."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import admin, ondemand, strm_inventory
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
) -> tuple[int, Any] | None:
    db = app.db_path
    if len(parts) == 2 and parts[1] == "search":
        kind = params.get("kind") or "movie"
        result = admin.tmdb_search(
            app.config,
            params.get("q") or "",
            kind="series" if kind == "series" else "movie",
            year=_int(params.get("year"), 0) or None,
            page=_int(params.get("page"), 1),
        )
        downloaded_ids = ondemand.downloaded_asset_ids(app.db_path)
        keys = strm_inventory.inventory(app.config.catalog_library)["keys"]
        for card in result.get("results") or []:
            if not card.get("tmdb"):
                continue
            kind_card = card.get("kind") or "movie"
            tmdb = int(card["tmdb"])
            lib = app.arr.in_library(kind_card, tmdb)
            card["in_library"] = bool(lib.get("in_library"))
            card["library_has_file"] = bool(lib.get("has_file"))
            with closing(connect(app.db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT m.id, m.key,
                           EXISTS(SELECT 1 FROM releases r
                                  WHERE r.asset_id = m.id) AS has_release
                    FROM media_items m
                    WHERE m.kind = ? AND m.tmdb = ?
                    """,
                    (kind_card, tmdb),
                ).fetchall()
            if not rows:
                card["catalog_state"] = None
                card["catalog_downloaded"] = False
                continue
            card["catalog_downloaded"] = any(
                int(row["id"]) in downloaded_ids for row in rows
            )
            if any(
                strm_inventory.is_key_published(dict(row), keys)
                for row in rows
            ):
                card["catalog_state"] = "publicado"
            elif any(int(row["has_release"] or 0) for row in rows):
                card["catalog_state"] = "pendiente"
            else:
                card["catalog_state"] = "sin_fuente"
        return 200, result
    if len(parts) == 3 and parts[1] == "seasons":
        result = admin.tmdb_seasons(app.config, _int(parts[2], 0))
        return 200, result
    if len(parts) == 4 and parts[1] == "episodes":
        result = admin.tmdb_episodes(
            app.config, _int(parts[2], 0), _int(parts[3], 0)
        )
        return 200, result
    return None
