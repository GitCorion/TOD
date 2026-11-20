"""Worker de salud en solo lectura.

Consulta Prowlarr por el título del activo, compara el resultado con el
release guardado (título + tamaño + hash/tracker) y actualiza salud/peers.
No descarga .torrent y no añade nada a qBittorrent.
"""

from __future__ import annotations

import re
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .db import connect
from .health import apply_health, classify_health
from .importer import norm_title
from .prowlarr import ProwlarrClient, ProwlarrError

EXT = re.compile(r"\.(mkv|mp4|avi|m4v|ts|m2ts)$", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    result: dict
    score: int


def clean_title(value: str | None) -> str:
    return norm_title(EXT.sub("", value or ""))


def size_close(
    left: int | None, right: int | None, *, tolerance: float = 0.02
) -> bool:
    if not left or not right:
        return False
    delta = abs(int(left) - int(right))
    return delta <= max(2 * 1024**2, int(max(left, right) * tolerance))


def score_match(release: Any, result: dict) -> int:
    """Puntúa si `result` corresponde al release guardado.

    Es deliberadamente estricto: el título normalizado y el tamaño deben
    coincidir, o el info_hash. Un mismo título con otro tamaño no prueba
    que el torrent guardado siga vivo.
    """
    rel_hash = (release["info_hash"] or "").lower()
    res_hash = (
        result.get("infoHash") or result.get("info_hash") or ""
    ).lower()
    if rel_hash and res_hash and rel_hash == res_hash:
        return 1000

    rel_title = clean_title(release["title"])
    res_title = clean_title(result.get("title"))
    if not rel_title or not res_title:
        return 0

    exact = rel_title == res_title
    contains = (
        len(rel_title) >= 16
        and len(res_title) >= 16
        and (rel_title in res_title or res_title in rel_title)
    )
    rel_size = release["size_bytes"]
    res_size = result.get("size")
    exact_size = bool(rel_size and res_size and int(rel_size) == int(res_size))
    close_size = size_close(rel_size, res_size)

    score = 0
    if exact and exact_size:
        score = 250
    elif exact and close_size:
        score = 200
    elif contains and exact_size:
        score = 150
    elif contains and close_size:
        score = 100
    elif exact:
        score = 20
    elif contains:
        score = 10

    rel_tmdb = release["tmdb"] if "tmdb" in release.keys() else None
    res_tmdb = result.get("tmdbId") or 0
    if rel_tmdb and res_tmdb and int(rel_tmdb) == int(res_tmdb):
        score += 20
    if (result.get("indexer") or "") == (release["tracker"] or ""):
        score += 5
    return score


def best_match(release: Any, results: list[dict], *, threshold: int = 100) -> Match | None:
    best: Match | None = None
    for result in results:
        score = score_match(release, result)
        if best is None or score > best.score:
            best = Match(result, score)
    if best is None or best.score < threshold:
        return None
    return best


def build_query(asset: Any) -> str:
    title = (asset["asset_title"] or asset["title"] or "").strip()
    parts = [title]
    year = asset["asset_year"]
    if year and not re.search(rf"(?<!\d){int(year)}(?!\d)", title):
        parts.append(str(year))
    if asset["kind"] == "series" and asset["season"] is not None:
        season = int(asset["season"])
        episode = int(asset["episode"] or 0)
        if not re.search(rf"(?i)\bS{season:02d}E{episode:02d}\b", title):
            parts.append(f"S{season:02d}E{episode:02d}")
    return " ".join(part for part in parts if part)


def _pending(db_path: str, limit: int, tracker: str | None, key: str | None) -> list[Any]:
    query = """
        SELECT r.*, a.key, a.kind, a.title AS asset_title, a.year AS asset_year,
               a.season, a.episode, a.tmdb
        FROM releases r
        JOIN media_items a ON a.id = r.asset_id
        WHERE r.health IN ('unknown', 'stale')
    """
    params: list[Any] = []
    if tracker:
        query += " AND r.tracker = ?"
        params.append(tracker)
    if key:
        query += " AND a.key = ?"
        params.append(key)
    query += " ORDER BY COALESCE(r.health_checked_at, ''), r.id LIMIT ?"
    params.append(limit)
    with closing(connect(db_path)) as conn:
        return conn.execute(query, params).fetchall()


def run(
    db_path: str,
    client: ProwlarrClient,
    *,
    limit: int = 25,
    delay: float = 1.0,
    dry_run: bool = False,
    now: datetime | None = None,
    tracker: str | None = None,
    key: str | None = None,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    checked_at = current.isoformat()
    rows = _pending(db_path, limit, tracker, key)
    items: list[dict[str, Any]] = []
    counts = {"checked": 0, "alive": 0, "dead": 0, "errors": 0}

    for index, row in enumerate(rows):
        query = build_query(row)
        item: dict[str, Any] = {
            "release_id": row["id"],
            "key": row["key"],
            "tracker": row["tracker"],
            "query": query,
        }
        try:
            results = client.search(
                query, media_type=row["kind"], limit=50
            )
            match = best_match(row, results)
            if match is None:
                health = "dead"
                seeders, peers = 0, 0
                live_title = live_tracker = live_guid = live_date = None
                score = 0
            else:
                result = match.result
                seeders = int(result.get("seeders") or 0)
                peers = int(result.get("peers") or 0)
                health = classify_health(
                    seeders=seeders,
                    peers=peers,
                    checked_at=checked_at,
                    now=current,
                    ttl_hours=ttl_hours,
                )
                live_title = result.get("title")
                live_tracker = result.get("indexer")
                live_guid = result.get("guid")
                live_date = result.get("publishDate")
                score = match.score

            item.update(
                {
                    "results": len(results),
                    "score": score,
                    "health": health,
                    "seeders": seeders,
                    "peers": peers,
                    "live_title": (live_title or "")[:100] or None,
                }
            )
            if not dry_run:
                apply_health(
                    db_path,
                    row["id"],
                    seeders=seeders,
                    peers=peers,
                    checked_at=checked_at,
                    health=health,
                    live_title=live_title,
                    live_tracker=live_tracker,
                    live_guid=live_guid,
                    live_publish_date=live_date,
                )
            counts["checked"] += 1
            counts["alive" if health == "alive" else "dead"] += 1
        except ProwlarrError as exc:
            item["error"] = str(exc)
            counts["errors"] += 1
        items.append(item)
        if delay and index < len(rows) - 1:
            time.sleep(delay)

    return {
        "checked": counts["checked"],
        "alive": counts["alive"],
        "dead": counts["dead"],
        "errors": counts["errors"],
        "dry_run": dry_run,
        "items": items,
    }
