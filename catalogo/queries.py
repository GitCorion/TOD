"""Consultas de solo lectura para el panel de administración.

Nunca seleccionan `download_url` ni `debrid_link`: son secretos del tracker.
Las rutas locales solo se muestran en la ficha de un título, que es una vista
de administración en loopback.
"""

from __future__ import annotations

import json
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .retention import evictable, status as seed_status

PAGE_SIZE = 25
LOCAL_STATES = ("ready", "seeding")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _scalar(conn, query: str, params: tuple = ()) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row[0] or 0) if row is not None else 0


def _disk(path: str | Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return {"path": str(path), "ok": False, "error": str(exc)[:160]}
    used = usage.total - usage.free
    return {
        "path": str(path),
        "ok": True,
        "total": usage.total,
        "free": usage.free,
        "used": used,
        "used_pct": round(100.0 * used / usage.total, 1) if usage.total else 0,
    }


UNBLOCKED_RELEASE_SQL = (
    "r.health <> 'blocked' "
    "AND NOT EXISTS ("
    "SELECT 1 FROM tracker_policies tp "
    "WHERE tp.tracker = r.tracker AND tp.enabled = 0"
    ")"
)

LIVE_SOURCE_SQL = f"""(
    EXISTS (SELECT 1 FROM releases r WHERE r.asset_id=m.id
            AND r.health='alive' AND {UNBLOCKED_RELEASE_SQL})
    OR EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id AND (
        (f.storage_location='LOCAL_DISK' AND f.state IN ('ready','seeding'))
        OR (f.storage_location='1FICHIER' AND f.state='archived')
    ))
    OR EXISTS (SELECT 1 FROM library_files lf WHERE lf.kind=m.kind
               AND lf.tmdb=m.tmdb AND (m.kind='movie' OR
               (lf.season=m.season AND lf.episode=m.episode)))
)"""


def overview(
    db_path: str | Path,
    *,
    config: Any = None,
    qbit: Any = None,
    qbit_error: str | None = None,
) -> dict[str, Any]:
    with closing(connect(db_path)) as conn:
        media = {
            "total": _scalar(conn, "SELECT COUNT(*) FROM media_items"),
            "movies": _scalar(
                conn, "SELECT COUNT(*) FROM media_items WHERE kind='movie'"
            ),
            "series": _scalar(
                conn, "SELECT COUNT(*) FROM media_items WHERE kind='series'"
            ),
            "cold": _scalar(
                conn, "SELECT COUNT(*) FROM media_items WHERE status='COLD'"
            ),
            "downloading": _scalar(
                conn,
                "SELECT COUNT(*) FROM media_items "
                "WHERE status IN ('ACTIVATING','DOWNLOADING_LOCAL')",
            ),
            "seeding": _scalar(
                conn, "SELECT COUNT(*) FROM media_items WHERE status='SEEDING_LOCAL'"
            ),
            "en_archivo": _scalar(
                conn, "SELECT COUNT(*) FROM media_items WHERE status='ON_1FICHIER'"
            ),
            "with_releases": _scalar(
                conn,
                "SELECT COUNT(*) FROM media_items m WHERE EXISTS "
                "(SELECT 1 FROM releases r WHERE r.asset_id=m.id)",
            ),
            "with_local": _scalar(
                conn,
                "SELECT COUNT(*) FROM media_items m WHERE EXISTS "
                "(SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id "
                "AND f.storage_location='LOCAL_DISK' "
                "AND f.state IN ('ready','seeding'))",
            ),
            "with_cloud": _scalar(
                conn,
                "SELECT COUNT(DISTINCT asset_id) FROM torrent_files "
                "WHERE storage_location='1FICHIER' AND state='archived'",
            ),
            "with_live_release": _scalar(
                conn,
                "SELECT COUNT(DISTINCT asset_id) FROM releases "
                "WHERE health='alive'",
            ),
            "with_source": _scalar(
                conn,
                f"SELECT COUNT(*) FROM media_items m WHERE {LIVE_SOURCE_SQL}",
            ),
            "in_library": _scalar(
                conn,
                "SELECT COUNT(DISTINCT m.id) FROM media_items m "
                "JOIN library_files lf ON lf.kind=m.kind AND lf.tmdb=m.tmdb "
                "AND (m.kind='movie' OR "
                "(lf.season=m.season AND lf.episode=m.episode))",
            ),
            "targets": _scalar(conn, "SELECT COUNT(*) FROM play_targets"),
        }
        media["without_releases"] = media["total"] - media["with_releases"]

        by_health = {
            row["health"]: int(row["n"])
            for row in conn.execute(
                "SELECT health, COUNT(*) AS n FROM releases GROUP BY health"
            )
        }
        releases = {
            "total": sum(by_health.values()),
            "by_health": by_health,
            "alive": by_health.get("alive", 0),
            "blocked": by_health.get("blocked", 0),
            "unknown": by_health.get("unknown", 0),
            "stale": by_health.get("stale", 0),
            "dead": by_health.get("dead", 0),
            "private": _scalar(
                conn, "SELECT COUNT(*) FROM releases WHERE privacy='private'"
            ),
            "public": _scalar(
                conn, "SELECT COUNT(*) FROM releases WHERE privacy='public'"
            ),
        }

        files = {
            "total": _scalar(conn, "SELECT COUNT(*) FROM torrent_files"),
            "local": _scalar(
                conn,
                "SELECT COUNT(*) FROM torrent_files "
                "WHERE storage_location='LOCAL_DISK'",
            ),
            "cloud": _scalar(
                conn,
                "SELECT COUNT(*) FROM torrent_files "
                "WHERE storage_location='1FICHIER'",
            ),
            "local_bytes": _scalar(
                conn,
                "SELECT COALESCE(SUM(size_bytes),0) FROM torrent_files "
                "WHERE storage_location='LOCAL_DISK'",
            ),
            "cloud_bytes": _scalar(
                conn,
                "SELECT COALESCE(SUM(size_bytes),0) FROM torrent_files "
                "WHERE storage_location='1FICHIER'",
            ),
        }

        torrent_states = {
            row["state"]: int(row["n"])
            for row in conn.execute(
                "SELECT state, COUNT(*) AS n FROM torrents GROUP BY state"
            )
        }
        downloads = {
            "by_state": torrent_states,
            "active": _scalar(
                conn,
                "SELECT COUNT(*) FROM torrents "
                "WHERE state='downloading' OR lower(COALESCE(qbit_state,'')) "
                "LIKE '%dl%'",
            ),
            "seeding": _scalar(
                conn, "SELECT COUNT(*) FROM torrents WHERE state='seeding'"
            ),
            "paused": _scalar(
                conn, "SELECT COUNT(*) FROM torrents WHERE state='paused'"
            ),
            "error": _scalar(
                conn, "SELECT COUNT(*) FROM torrents WHERE state='error'"
            ),
            "ondemand": _scalar(
                conn, "SELECT COUNT(*) FROM torrents WHERE category='ondemand'"
            ),
        }

        commitments = {
            "total": _scalar(conn, "SELECT COUNT(*) FROM seed_commitments"),
            "approved": _scalar(
                conn,
                "SELECT COUNT(*) FROM seed_commitments WHERE state='approved'",
            ),
            "active": _scalar(
                conn,
                "SELECT COUNT(*) FROM seed_commitments "
                "WHERE state IN ('active','seeding')",
            ),
            "done": _scalar(
                conn, "SELECT COUNT(*) FROM seed_commitments WHERE state='done'"
            ),
            "evictable": len(evictable(str(db_path))),
        }
        jobs = {
            "queued": _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE state='queued'"),
            "running": _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE state='running'"),
            "error": _scalar(
                conn,
                "SELECT COUNT(*) FROM jobs WHERE state='error' "
                "AND finished_at >= datetime('now', '-7 day')",
            ),
        }
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT id, ts, kind, asset_id, payload FROM events "
                "ORDER BY id DESC LIMIT 12"
            )
        ]

    disks = []
    if config is not None:
        seen: set[str] = set()
        library_roots = (
            config.library_roots()
            if hasattr(config, "library_roots")
            else (getattr(config, "library_root", None),)
        )
        for path in (
            getattr(config, "catalog_library", None),
            getattr(config, "seed_root", None),
            *library_roots,
        ):
            if not path:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            disks.append(_disk(key))

    live = {"ok": False, "error": qbit_error}
    if qbit is not None:
        try:
            torrents = qbit.torrents()
            downloading = sum(
                1
                for item in torrents
                if float(item.get("progress") or 0) < 1
                and "paused" not in (item.get("state") or "").lower()
            )
            seeding = sum(
                1
                for item in torrents
                if float(item.get("progress") or 0) >= 1
                or "seed" in (item.get("state") or "").lower()
            )
            live = {
                "ok": True,
                "version": qbit.app_version(),
                "torrents": len(torrents),
                "downloading": downloading,
                "seeding": seeding,
                "dlspeed": sum(int(item.get("dlspeed") or 0) for item in torrents),
                "upspeed": sum(int(item.get("upspeed") or 0) for item in torrents),
            }
        except Exception as exc:
            live = {"ok": False, "error": str(exc)[:200]}

    return {
        "media": media,
        "releases": releases,
        "files": files,
        "downloads": downloads,
        "commitments": commitments,
        "jobs": jobs,
        "events": events,
        "disk": disks,
        "qbit": live,
        "generated_at": _now().isoformat(),
    }


AVAILABILITY_SQL = {
    "live": LIVE_SOURCE_SQL,
    "none": f"""(
        NOT EXISTS (SELECT 1 FROM releases r WHERE r.asset_id=m.id
                    AND {UNBLOCKED_RELEASE_SQL})
        AND NOT EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id
                        AND f.storage_location IN ('LOCAL_DISK','1FICHIER')
                        AND f.state NOT IN ('evicted','missing'))
        AND NOT EXISTS (SELECT 1 FROM library_files lf WHERE lf.kind=m.kind
                        AND lf.tmdb=m.tmdb AND (m.kind='movie' OR
                        (lf.season=m.season AND lf.episode=m.episode)))
    )""",
    "local": """EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id
                        AND f.storage_location='LOCAL_DISK'
                        AND f.state IN ('ready','seeding'))""",
    "cloud": """EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id
                       AND f.storage_location='1FICHIER'
                       AND f.state='archived')""",
    "library": """EXISTS (SELECT 1 FROM library_files lf WHERE lf.kind=m.kind
                          AND lf.tmdb=m.tmdb AND (m.kind='movie' OR
                          (lf.season=m.season AND lf.episode=m.episode)))""",
    "downloading": """(m.status IN ('ACTIVATING','DOWNLOADING_LOCAL')
        OR EXISTS (SELECT 1 FROM torrents t JOIN releases r ON r.id=t.release_id
                   WHERE r.asset_id=m.id AND t.state='downloading'))""",
    "unverified": f"""(
        EXISTS (SELECT 1 FROM releases r WHERE r.asset_id=m.id
                AND {UNBLOCKED_RELEASE_SQL})
        AND NOT {LIVE_SOURCE_SQL}
    )""",
}


def list_titles(
    db_path: str | Path,
    *,
    q: str = "",
    kind: str = "",
    status: str = "",
    availability: str = "",
    page: int = 1,
    per_page: int = PAGE_SIZE,
    sort: str = "popularity",
) -> dict[str, Any]:
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 100))
    conditions: list[str] = []
    params: list[Any] = []
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        conditions.append(
            "(m.title LIKE ? OR m.original_title LIKE ? OR m.aliases LIKE ? "
            "OR m.key LIKE ?)"
        )
        params.extend([like, like, like, like])
    if kind in ("movie", "series"):
        conditions.append("m.kind = ?")
        params.append(kind)
    if status:
        conditions.append("m.status = ?")
        params.append(status)
    if availability in AVAILABILITY_SQL:
        conditions.append(AVAILABILITY_SQL[availability])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    order = {
        "title": "m.title COLLATE NOCASE ASC, m.year DESC",
        "recent": "m.updated_at DESC, m.id DESC",
        "year": "m.year DESC, m.title COLLATE NOCASE ASC",
        "popularity": "COALESCE(m.popularity,0) DESC, m.id DESC",
    }.get(sort, "COALESCE(m.popularity,0) DESC, m.id DESC")

    base = f"""
        SELECT m.id, m.key, m.kind, m.tmdb, m.title, m.year, m.season,
               m.episode, m.status, m.updated_at, m.poster_path,
               (SELECT COUNT(*) FROM releases r WHERE r.asset_id=m.id
                   AND {UNBLOCKED_RELEASE_SQL})
                   AS releases,
               (SELECT COUNT(*) FROM releases r WHERE r.asset_id=m.id
                   AND r.health='alive' AND {UNBLOCKED_RELEASE_SQL}) AS alive,
               (SELECT MAX(r.seeders) FROM releases r WHERE r.asset_id=m.id
                   AND {UNBLOCKED_RELEASE_SQL})
                   AS seeders,
               (SELECT r.quality FROM releases r WHERE r.asset_id=m.id
                   AND r.health='alive' AND {UNBLOCKED_RELEASE_SQL}
                   ORDER BY COALESCE(r.seeders,0) DESC LIMIT 1) AS quality,
               (SELECT r.audio FROM releases r WHERE r.asset_id=m.id
                   AND r.health='alive' AND {UNBLOCKED_RELEASE_SQL}
                   ORDER BY COALESCE(r.seeders,0) DESC LIMIT 1) AS audio,
               EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id
                   AND f.storage_location='LOCAL_DISK'
                   AND f.state IN ('ready','seeding')) AS has_local,
               EXISTS (SELECT 1 FROM torrent_files f WHERE f.asset_id=m.id
                   AND f.storage_location='1FICHIER'
                   AND f.state='archived') AS has_cloud,
               EXISTS (SELECT 1 FROM library_files lf WHERE lf.kind=m.kind
                   AND lf.tmdb=m.tmdb AND (m.kind='movie' OR
                   (lf.season=m.season AND lf.episode=m.episode))) AS in_library,
               EXISTS (SELECT 1 FROM play_targets pt WHERE pt.asset_id=m.id)
                   AS has_target
        FROM media_items m
    """
    with closing(connect(db_path)) as conn:
        total = _scalar(
            conn, "SELECT COUNT(*) FROM media_items m" + where, tuple(params)
        )
        rows = conn.execute(
            base + where + f" ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for field in (
            "has_local",
            "has_cloud",
            "in_library",
            "has_target",
        ):
            item[field] = bool(item.get(field))
        if item["has_local"]:
            item["availability"] = "local"
        elif item["in_library"]:
            item["availability"] = "library"
        elif item["has_cloud"]:
            item["availability"] = "cloud"
        elif item["alive"]:
            item["availability"] = "alive"
        elif item["releases"]:
            item["availability"] = "unverified"
        else:
            item["availability"] = "none"
        items.append(item)
    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
    }


def _release_rows(conn, asset_id: int) -> list[dict[str, Any]]:
    from . import release_evaluator

    asset_row = conn.execute(
        "SELECT * FROM media_items WHERE id = ?", (int(asset_id),)
    ).fetchone()
    db_file = conn.execute("PRAGMA database_list").fetchone()
    db_path = db_file[2] if db_file and len(db_file) > 2 else ""
    ctx = None
    if asset_row is not None and db_path:
        try:
            ctx = release_evaluator.load_evaluation_context(
                conn, db_path, dict(asset_row)
            )
        except Exception:
            ctx = None

    rows = conn.execute(
        f"""
        SELECT r.id, r.tracker, r.title, r.quality, r.codec, r.audio,
               r.source_type, r.hdr, r.audio_codec, r.audio_channels,
               r.edition, r.release_group, r.proper_rank, r.streaming_service,
               r.audio_confidence, r.size_bytes, r.seeders, r.peers,
               r.health, r.health_checked_at, r.privacy, r.source,
               r.is_pack, r.info_hash, r.info_url,
               r.created_at, r.updated_at,
               ss.started_ok AS swarm_started_ok,
               ss.failures AS swarm_failures,
               t.state AS torrent_state, t.qbit_state AS torrent_qbit_state,
               t.progress AS torrent_progress, t.ratio AS torrent_ratio,
               t.save_path AS torrent_save_path
        FROM releases r
        LEFT JOIN swarm_stats ss ON ss.release_id = r.id
        LEFT JOIN torrents t ON t.id = (
            SELECT id FROM torrents WHERE release_id = r.id
            ORDER BY id DESC LIMIT 1
        )
        WHERE r.asset_id = ?
          AND {UNBLOCKED_RELEASE_SQL}
        """,
        (int(asset_id),),
    ).fetchall()
    result: list[tuple[tuple, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        if ctx is not None:
            ev = release_evaluator.evaluate_release(
                item,
                ctx["asset"],
                ctx["profile"],
                global_language=ctx["global_language"],
                custom_formats=ctx["custom_formats"],
                restrictions=ctx["restrictions"],
                asset_tag_ids=ctx["asset_tag_ids"],
                blocked_trackers=ctx["blocked_trackers"],
                failed_release_reasons=ctx["failed_release_reasons"],
            )
            item.update(ev.as_dict())
            sort_k = ev.sort_key
        else:
            item["allowed"] = True
            item["rejections"] = []
            item["cf_score"] = 0
            item["score_breakdown"] = []
            sort_k = (0, 0, -(item.get("seeders") or 0), item.get("size_bytes") or 0)
        result.append((sort_k, item))
    result.sort(key=lambda pair: pair[0])
    return [item for _, item in result]


def title_detail(
    db_path: str | Path,
    asset_id: int,
    *,
    qbit: Any = None,
) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        asset = conn.execute(
            "SELECT * FROM media_items WHERE id = ?", (int(asset_id),)
        ).fetchone()
        if asset is None:
            return None
        item = dict(asset)
        item["aliases"] = _json(item.get("aliases"))
        target = conn.execute(
            "SELECT * FROM play_targets WHERE asset_id = ?", (int(asset_id),)
        ).fetchone()
        files = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, release_id, torrent_id, file_index, rel_path,
                       local_path, library_path, size_bytes, state,
                       storage_location, downloaded_bytes, cloud_uploaded_at,
                       priority, last_seen
                FROM torrent_files WHERE asset_id = ?
                ORDER BY storage_location, file_index, id
                """,
                (int(asset_id),),
            )
        ]
        commitments = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.id, i.tracker, i.state, i.size_bytes,
                       i.seed_required_until, i.target_ratio, i.created_at,
                       i.approved_at, i.started_at, i.completed_at,
                       t.ratio AS live_ratio, t.progress AS live_progress
                FROM seed_commitments i
                LEFT JOIN torrents t ON t.info_hash = i.info_hash
                WHERE i.asset_id = ?
                ORDER BY i.id DESC
                """,
                (int(asset_id),),
            )
        ]
        library = [
            dict(row)
            for row in conn.execute(
                "SELECT path, size_bytes, source, updated_at FROM library_files "
                "WHERE kind=? AND tmdb=? AND (?='movie' OR "
                "(season=? AND episode=?))",
                (
                    item["kind"],
                    item["tmdb"],
                    item["kind"],
                    int(item["season"] or 0),
                    int(item["episode"] or 0),
                ),
            )
        ]
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT id, ts, kind, payload FROM events "
                "WHERE asset_id = ? ORDER BY id DESC LIMIT 30",
                (int(asset_id),),
            )
        ]
        play_intents = [
            dict(row)
            for row in conn.execute(
                "SELECT id, source, created_at, expires_at, consumed_at "
                "FROM play_intents WHERE media_id=? ORDER BY id DESC LIMIT 10",
                (item["key"],),
            )
        ]
        releases = _release_rows(conn, int(asset_id))

    status = None
    if qbit is not None:
        try:
            from .status import asset_status

            status = asset_status(
                str(db_path), item["key"], qbit=qbit
            )
        except Exception as exc:
            status = {"error": str(exc)[:200]}

    return {
        "item": item,
        "releases": releases,
        "files": files,
        "commitments": commitments,
        "target": dict(target) if target is not None else None,
        "library": library,
        "events": events,
        "play_intents": play_intents,
        "status": status,
        "tmdb_url": (
            f"https://www.themoviedb.org/{'movie' if item['kind']=='movie' else 'tv'}"
            f"/{item['tmdb']}"
            if item.get("tmdb")
            else None
        ),
    }


def list_downloads(
    db_path: str | Path,
    qbit: Any = None,
    *,
    category: str | None = None,
) -> dict[str, Any]:
    error = None
    live: list[dict[str, Any]] = []
    if qbit is not None:
        try:
            live = qbit.torrents(category=category) if category else qbit.torrents()
        except Exception as exc:
            error = str(exc)[:200]
    with closing(connect(db_path)) as conn:
        condition = "t.state <> 'removed'"
        params: list[Any] = []
        if category:
            condition += " AND t.category = ?"
            params.append(category)
        rows = conn.execute(
            f"""
            SELECT t.info_hash, t.state, t.qbit_state, t.progress, t.ratio,
                   t.category, t.save_path, t.added_at, t.updated_at,
                   r.id AS release_id, r.title AS release_title,
                   r.tracker, r.quality, r.privacy, r.size_bytes,
                   m.id AS media_id, m.key, m.title, m.year, m.kind,
                   m.season, m.episode, m.poster_path
            FROM torrents t
            LEFT JOIN releases r ON r.id = t.release_id
            LEFT JOIN media_items m ON m.id = r.asset_id
            WHERE {condition}
            """,
            params,
        ).fetchall()
    by_hash = {(row["info_hash"] or "").lower(): dict(row) for row in rows}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for torrent in live:
        info_hash = (torrent.get("hash") or "").lower()
        if not info_hash:
            continue
        seen.add(info_hash)
        db_row = by_hash.get(info_hash, {})
        item = {
            "info_hash": info_hash,
            "name": torrent.get("name") or db_row.get("release_title"),
            "category": torrent.get("category"),
            "state": torrent.get("state"),
            "progress": float(torrent.get("progress") or 0),
            "size": int(torrent.get("size") or 0),
            "downloaded": int(torrent.get("downloaded") or 0),
            "dlspeed": int(torrent.get("dlspeed") or 0),
            "upspeed": int(torrent.get("upspeed") or 0),
            "num_seeds": int(torrent.get("num_seeds") or 0),
            "num_leechs": int(torrent.get("num_leechs") or 0),
            "num_complete": int(torrent.get("num_complete") or 0),
            "eta": int(torrent.get("eta") or 0),
            "ratio": float(torrent.get("ratio") or 0),
            "save_path": torrent.get("save_path"),
            "live": True,
            **{
                key: db_row.get(key)
                for key in (
                    "release_id",
                    "release_title",
                    "tracker",
                    "quality",
                    "privacy",
                    "media_id",
                    "key",
                    "title",
                    "year",
                    "kind",
                    "season",
                    "episode",
                    "poster_path",
                )
            },
        }
        items.append(item)
    for info_hash, row in by_hash.items():
        if info_hash in seen:
            continue
        items.append(
            {
                **row,
                "info_hash": info_hash,
                "name": row.get("release_title"),
                "live": False,
                "dlspeed": 0,
                "upspeed": 0,
                "num_seeds": 0,
                "num_leechs": 0,
                "num_complete": 0,
                "downloaded": 0,
                "size": int(row.get("size_bytes") or 0),
                "progress": float(row.get("progress") or 0),
            }
        )
    items.sort(
        key=lambda item: (
            0 if item.get("live") and item.get("progress", 0) < 1 else 1,
            str(item.get("name") or "").lower(),
        )
    )
    return {"items": items, "qbit_error": error, "live": bool(live)}


def list_seeds(
    db_path: str | Path,
    *,
    limit: int = 100,
    qbit: Any = None,
) -> dict[str, Any]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.tracker, i.state, i.size_bytes,
                   i.seed_required_until, i.target_ratio, i.created_at,
                   i.started_at, i.completed_at, i.reason, i.info_hash,
                   COALESCE(i.info_hash,
                            (SELECT info_hash FROM torrents
                             WHERE release_id = i.release_id
                             ORDER BY id DESC LIMIT 1)) AS effective_hash,
                   m.id AS media_id, m.key, m.title, m.year, m.kind,
                   m.season, m.episode,
                   t.ratio AS live_ratio, t.progress AS live_progress,
                   t.state AS torrent_state, t.seed_started_at
            FROM seed_commitments i
            JOIN media_items m ON m.id = i.asset_id
            LEFT JOIN torrents t ON t.info_hash = i.info_hash
            WHERE i.state IN ('approved', 'active', 'seeding')
            ORDER BY CASE i.state
                        WHEN 'seeding' THEN 0 WHEN 'active' THEN 1
                        WHEN 'approved' THEN 2 ELSE 3 END,
                     i.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    live_by_hash: dict[str, dict] = {}
    qbit_ok = False
    if qbit is not None:
        try:
            live_by_hash = {
                str(torrent.get("hash") or "").lower(): torrent
                for torrent in qbit.torrents()
                if torrent.get("hash")
            }
            qbit_ok = True
        except Exception:
            live_by_hash = {}
            qbit_ok = False
    items = []
    for row in rows:
        item = dict(row)
        info_hash = (item.get("effective_hash") or item.get("info_hash") or "").lower()
        if info_hash:
            item["info_hash"] = info_hash
        live = live_by_hash.get(info_hash)
        if live is not None:
            item["live"] = True
            item["live_state"] = live.get("state")
            item["live_ratio"] = float(live.get("ratio") or 0)
            item["live_progress"] = float(live.get("progress") or 0)
            item["live_upspeed"] = int(live.get("upspeed") or 0)
            item["live_num_seeds"] = int(live.get("num_seeds") or 0)
            item["live_num_leechs"] = int(live.get("num_leechs") or 0)
            item["live_eta"] = int(live.get("eta") or 0)
            item["torrent_state"] = live.get("state")
        try:
            raw_live = str(item.get("live_state") or "").lower()
            live_seeding = bool(item.get("live")) and any(
                value in raw_live
                for value in ("uploading", "stalledup", "forcedup")
            )
            state = seed_status(
                str(db_path),
                int(item["id"]),
                state="seeding" if live_seeding else ("active" if item.get("live") else None),
                ratio=item.get("live_ratio") if item.get("live") else None,
                started=item.get("seed_started_at") or item.get("started_at"),
            )
            item["can_finish"] = bool(state and state.allowed)
            item["finish_reason"] = state.reason if state else None
        except Exception:
            item["can_finish"] = False
            item["finish_reason"] = None
        items.append(item)
    pending = [item for item in items if item.get("can_finish")]
    return {"items": items, "evictable": len(pending), "qbit_ok": qbit_ok}


def list_events(db_path: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, ts, kind, asset_id, payload FROM events "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(row) for row in rows]


def calendar(
    db_path: str | Path,
    *,
    days: int = 90,
    limit: int = 500,
    start: str | None = None,
    end: str | None = None,
    monitored_only: bool = False,
    kind: str = "",
) -> list[dict[str, Any]]:
    """Estrenos y emisiones fechados en TMDB, con soporte de rango o ventana."""
    days = max(1, min(int(days), 365))
    clauses = ["COALESCE(m.release_date, m.air_date) IS NOT NULL"]
    params: list[Any] = []
    if start and end:
        clauses.append("date(COALESCE(m.release_date, m.air_date)) >= date(?)")
        clauses.append("date(COALESCE(m.release_date, m.air_date)) <= date(?)")
        params.extend([start[:10], end[:10]])
    else:
        clauses.append("date(COALESCE(m.release_date, m.air_date)) >= date('now', '-7 day')")
        clauses.append("date(COALESCE(m.release_date, m.air_date)) <= date('now', ?)")
        params.append(f"+{days} days")
    if monitored_only:
        clauses.append("m.monitored = 1")
    if kind in ("movie", "series"):
        clauses.append("m.kind = ?")
        params.append(kind)
    params.append(max(1, min(int(limit), 1000)))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.key, m.title, m.episode_title, m.kind, m.tmdb,
                   m.season, m.episode, m.poster_path, m.release_date, m.air_date,
                   m.monitored, m.library_added,
                   EXISTS (
                       SELECT 1 FROM releases r
                       WHERE r.asset_id = m.id AND r.health = 'alive'
                   ) AS has_alive
            FROM media_items m
            WHERE {' AND '.join(clauses)}
            ORDER BY date(COALESCE(m.release_date, m.air_date)), m.title, m.season, m.episode
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [{**dict(row), "has_alive": bool(row["has_alive"])} for row in rows]


def list_trackers(db_path: str | Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT r.tracker,
                   COUNT(*) AS releases,
                   SUM(CASE WHEN r.health='alive' THEN 1 ELSE 0 END) AS alive,
                   SUM(CASE WHEN r.health='blocked' THEN 1 ELSE 0 END) AS blocked,
                   MAX(r.health_checked_at) AS last_checked,
                   p.privacy,
                   COALESCE(
                       p.enabled,
                       CASE WHEN SUM(CASE WHEN r.health='blocked' THEN 1 ELSE 0 END) > 0
                            THEN 0 ELSE 1 END
                   ) AS enabled,
                   p.min_seed_hours, p.target_ratio,
                   p.max_active, p.abandon_under_percent, p.abandon_action,
                   p.keep_for_bonus
            FROM releases r
            LEFT JOIN tracker_policies p ON p.tracker = r.tracker
            GROUP BY r.tracker
            ORDER BY releases DESC, r.tracker COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]
