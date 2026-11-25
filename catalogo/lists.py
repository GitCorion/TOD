"""Gestión estilo *Arr de Etiquetas, Custom Filters, Import Lists, Mass Edit, Blocklist y Cutoff."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import naming, profiles, release_evaluator
from .db import connect
from .title_sync import upsert_episodes, upsert_movies
from .tmdb import TmdbClient, episode_items, movie_item, tv_item

MONITOR_PRESETS = (
    "all",
    "future",
    "missing",
    "first_season",
    "latest_season",
    "pilot",
    "none",
    "manual",
)

IMPORT_KINDS = (
    "tmdb_collection",
    "tmdb_list",
    "tmdb_popular",
    "tmdb_discover",
    "url",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


# ═══════════════════════════════════════════════════════════════════════
# TAGS (Etiquetas)
# ═══════════════════════════════════════════════════════════════════════


def list_tags(db_path: str | Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "tags"):
            return []
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.color, t.created_at,
                   COUNT(DISTINCT mt.media_kind || ':' || mt.tmdb) AS media_count
            FROM tags t
            LEFT JOIN media_tags mt ON mt.tag_id = t.id
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def save_tag(
    db_path: str | Path,
    name: str,
    color: str = "#6b7280",
    *,
    tag_id: int | None = None,
) -> dict[str, Any]:
    clean = str(name or "").strip().lower()
    if not clean:
        raise ValueError("nombre de etiqueta obligatorio")
    clean_color = str(color or "#6b7280").strip() or "#6b7280"
    with closing(connect(db_path)) as conn:
        if tag_id is not None:
            conn.execute(
                "UPDATE tags SET name = ?, color = ? WHERE id = ?",
                (clean, clean_color, int(tag_id)),
            )
            tid = int(tag_id)
        else:
            existing = conn.execute(
                "SELECT id FROM tags WHERE name = ?", (clean,)
            ).fetchone()
            if existing is not None:
                tid = int(existing["id"])
                conn.execute(
                    "UPDATE tags SET color = ? WHERE id = ?",
                    (clean_color, tid),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO tags(name, color) VALUES (?, ?)",
                    (clean, clean_color),
                )
                tid = int(cur.lastrowid)
        conn.commit()
    return next((t for t in list_tags(db_path) if t["id"] == tid), {})


def delete_tag(db_path: str | Path, tag_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (int(tag_id),))
        conn.commit()
        return cur.rowcount > 0


def media_tags_map(db_path: str | Path) -> dict[tuple[str, int], list[dict[str, Any]]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "media_tags"):
            return {}
        rows = conn.execute(
            """
            SELECT mt.media_kind, mt.tmdb, t.id, t.name, t.color
            FROM media_tags mt
            JOIN tags t ON t.id = mt.tag_id
            ORDER BY t.name COLLATE NOCASE ASC
            """
        ).fetchall()
    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault((str(r["media_kind"]), int(r["tmdb"])), []).append(
            {"id": int(r["id"]), "name": r["name"], "color": r["color"]}
        )
    return out


def get_media_tags(db_path: str | Path, media_kind: str, tmdb: int) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "media_tags"):
            return []
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.color
            FROM media_tags mt
            JOIN tags t ON t.id = mt.tag_id
            WHERE mt.media_kind = ? AND mt.tmdb = ?
            ORDER BY t.name COLLATE NOCASE ASC
            """,
            (media_kind, int(tmdb)),
        ).fetchall()
        return [dict(r) for r in rows]


def set_media_tags(
    db_path: str | Path,
    media_kind: str,
    tmdb: int,
    tag_ids: list[int],
) -> list[dict[str, Any]]:
    if media_kind not in ("movie", "series"):
        raise ValueError("media_kind inválido")
    clean_ids = sorted({int(x) for x in (tag_ids or []) if str(x).isdigit()})
    with closing(connect(db_path)) as conn:
        conn.execute(
            "DELETE FROM media_tags WHERE media_kind = ? AND tmdb = ?",
            (media_kind, int(tmdb)),
        )
        for tid in clean_ids:
            conn.execute(
                "INSERT OR IGNORE INTO media_tags(media_kind, tmdb, tag_id) VALUES (?, ?, ?)",
                (media_kind, int(tmdb), tid),
            )
        conn.commit()
    return get_media_tags(db_path, media_kind, tmdb)


# ═══════════════════════════════════════════════════════════════════════
# CUSTOM FILTERS
# ═══════════════════════════════════════════════════════════════════════


def list_custom_filters(db_path: str | Path, *, target: str | None = None) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "custom_filters"):
            return []
        sql = "SELECT * FROM custom_filters"
        params: list[Any] = []
        if target in ("catalog", "wanted"):
            sql += " WHERE target = ?"
            params.append(target)
        sql += " ORDER BY is_pinned DESC, name COLLATE NOCASE ASC"
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        try:
            item["filters"] = json.loads(item.get("filters_json") or "{}")
        except (TypeError, ValueError):
            item["filters"] = {}
        item["is_pinned"] = bool(item.get("is_pinned", 1))
        out.append(item)
    return out


def save_custom_filter(
    db_path: str | Path,
    data: dict[str, Any],
    *,
    filter_id: int | None = None,
) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("nombre de filtro obligatorio")
    target = str(data.get("target") or "catalog")
    if target not in ("catalog", "wanted"):
        raise ValueError("target inválido")
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    filters_json = json.dumps(filters, ensure_ascii=False)
    is_pinned = 1 if data.get("is_pinned", True) else 0
    with closing(connect(db_path)) as conn:
        if filter_id is not None:
            conn.execute(
                """
                UPDATE custom_filters
                SET name = ?, target = ?, filters_json = ?, is_pinned = ?
                WHERE id = ?
                """,
                (name, target, filters_json, is_pinned, int(filter_id)),
            )
            fid = int(filter_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO custom_filters(name, target, filters_json, is_pinned)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    target = excluded.target,
                    filters_json = excluded.filters_json,
                    is_pinned = excluded.is_pinned
                """,
                (name, target, filters_json, is_pinned),
            )
            fid = int(cur.lastrowid) or int(
                conn.execute("SELECT id FROM custom_filters WHERE name = ?", (name,)).fetchone()["id"]
            )
        conn.commit()
    return next((f for f in list_custom_filters(db_path) if f["id"] == fid), {})


def delete_custom_filter(db_path: str | Path, filter_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute("DELETE FROM custom_filters WHERE id = ?", (int(filter_id),))
        conn.commit()
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════════════
# SERIES SETTINGS & MONITOR PRESETS
# ═══════════════════════════════════════════════════════════════════════


def get_series_settings(db_path: str | Path, tmdb: int) -> dict[str, Any]:
    tmdb = int(tmdb)
    with closing(connect(db_path)) as conn:
        ss = None
        if _table_exists(conn, "series_settings"):
            row = conn.execute(
                "SELECT * FROM series_settings WHERE tmdb = ?", (tmdb,)
            ).fetchone()
            if row:
                ss = dict(row)
        sample = conn.execute(
            """
            SELECT quality_profile_id, root_folder_id, series_type, is_anime,
                   series_status, genres, imdb_id, network_studio, certification
            FROM media_items
            WHERE kind = 'series' AND tmdb = ?
            LIMIT 1
            """,
            (tmdb,),
        ).fetchone()
    base = dict(sample) if sample else {}
    return {
        "tmdb": tmdb,
        "quality_profile_id": (ss or {}).get("quality_profile_id") or base.get("quality_profile_id"),
        "root_folder_id": (ss or {}).get("root_folder_id") or base.get("root_folder_id"),
        "monitor_mode": (ss or {}).get("monitor_mode") or "all",
        "series_type": (ss or {}).get("series_type") or base.get("series_type") or "standard",
        "season_folder": bool((ss or {}).get("season_folder", 1)),
        "language_override": (ss or {}).get("language_override"),
        "series_status": base.get("series_status"),
        "genres": base.get("genres"),
        "imdb_id": base.get("imdb_id"),
        "network_studio": base.get("network_studio"),
        "tags": get_media_tags(db_path, "series", tmdb),
    }


def save_series_settings(
    db_path: str | Path,
    tmdb: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    tmdb = int(tmdb)
    current = get_series_settings(db_path, tmdb)
    profile_id = (
        int(data["quality_profile_id"])
        if data.get("quality_profile_id") not in (None, "", 0, "0")
        else (None if "quality_profile_id" in data else current.get("quality_profile_id"))
    )
    rf_id = (
        int(data["root_folder_id"])
        if data.get("root_folder_id") not in (None, "", 0, "0")
        else (None if "root_folder_id" in data else current.get("root_folder_id"))
    )
    series_type = str(data.get("series_type") or current.get("series_type") or "standard")
    if series_type not in ("standard", "daily", "anime"):
        raise ValueError("series_type inválido")
    monitor_mode = str(data.get("monitor_mode") or current.get("monitor_mode") or "all")
    if monitor_mode not in MONITOR_PRESETS:
        raise ValueError("monitor_mode inválido")
    season_folder = 1 if data.get("season_folder", current.get("season_folder", True)) else 0
    lang_override = (
        str(data["language_override"]).strip() or None
        if "language_override" in data
        else current.get("language_override")
    )

    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO series_settings(
                tmdb, quality_profile_id, root_folder_id, monitor_mode,
                series_type, season_folder, language_override, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(tmdb) DO UPDATE SET
                quality_profile_id = excluded.quality_profile_id,
                root_folder_id = excluded.root_folder_id,
                monitor_mode = excluded.monitor_mode,
                series_type = excluded.series_type,
                season_folder = excluded.season_folder,
                language_override = excluded.language_override,
                updated_at = excluded.updated_at
            """,
            (tmdb, profile_id, rf_id, monitor_mode, series_type, season_folder, lang_override),
        )
        conn.execute(
            """
            UPDATE media_items SET
                quality_profile_id = ?,
                root_folder_id = ?,
                series_type = ?,
                is_anime = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE kind = 'series' AND tmdb = ?
            """,
            (profile_id, rf_id, series_type, 1 if series_type == "anime" else 0, tmdb),
        )
        conn.commit()
    if "tag_ids" in data and isinstance(data["tag_ids"], list):
        set_media_tags(db_path, "series", tmdb, data["tag_ids"])
    return get_series_settings(db_path, tmdb)


def apply_series_monitor_preset(
    db_path: str | Path,
    tmdb: int,
    preset: str,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    """Aplica un preset estilo Sonarr a los episodios de la serie."""
    tmdb = int(tmdb)
    if preset not in MONITOR_PRESETS:
        raise ValueError(f"preset inválido: {preset}")
    today = date.today().isoformat()
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.key, m.season, m.episode, m.air_date, m.library_added,
                   EXISTS (
                       SELECT 1 FROM library_files lf
                       WHERE lf.kind = 'series' AND lf.tmdb = m.tmdb
                         AND lf.season = m.season AND lf.episode = m.episode
                   ) AS has_lib,
                   EXISTS (
                       SELECT 1 FROM torrent_files tf
                       WHERE tf.asset_id = m.id
                         AND tf.state IN ('ready', 'archived', 'seeding', 'complete')
                   ) AS has_local
            FROM media_items m
            WHERE m.kind = 'series' AND m.tmdb = ?
            ORDER BY m.season ASC, m.episode ASC
            """,
            (tmdb,),
        ).fetchall()
        regular_seasons = sorted({int(r["season"] or 0) for r in rows if int(r["season"] or 0) > 0})
        first_season = min(regular_seasons) if regular_seasons else 1
        latest_season = max(regular_seasons) if regular_seasons else 1

        updates: list[tuple[int, str, str, int]] = []
        stamp = _now()
        monitored_count = 0
        for r in rows:
            s = int(r["season"] or 0)
            e = int(r["episode"] or 0)
            air = str(r["air_date"] or "")[:10]
            has_file = bool(r["has_lib"] or r["has_local"])
            if preset == "all":
                want = s > 0
            elif preset == "future":
                want = s > 0 and (not air or air > today)
            elif preset == "missing":
                want = s > 0 and not has_file
            elif preset == "first_season":
                want = s == first_season
            elif preset == "latest_season":
                want = s == latest_season
            elif preset == "pilot":
                want = s == first_season and e == 1
            elif preset == "none":
                want = False
            else:
                continue
            if want:
                monitored_count += 1
            updates.append((1 if want else 0, stamp, source, int(r["id"])))

        if updates:
            conn.executemany(
                """
                UPDATE media_items SET
                    monitored = ?,
                    monitored_at = CASE WHEN ? = 1 THEN COALESCE(monitored_at, ?) ELSE monitored_at END,
                    monitor_source = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                [(w, w, st, src, aid) for w, st, src, aid in updates],
            )
        if _table_exists(conn, "series_settings"):
            conn.execute(
                """
                INSERT INTO series_settings(tmdb, monitor_mode, updated_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(tmdb) DO UPDATE SET
                    monitor_mode = excluded.monitor_mode,
                    updated_at = excluded.updated_at
                """,
                (tmdb, preset),
            )
        conn.commit()
    return {
        "tmdb": tmdb,
        "preset": preset,
        "total": len(rows),
        "monitored": monitored_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# MASS EDITOR
# ═══════════════════════════════════════════════════════════════════════


def mass_edit_media(
    db_path: str | Path,
    keys: list[str],
    *,
    quality_profile_id: int | None = ...,
    root_folder_id: int | None = ...,
    monitored: bool | None = None,
    monitor_mode: str | None = None,
    add_tag_ids: list[int] | None = None,
    remove_tag_ids: list[int] | None = None,
    out_dir: str | Path | None = None,
    reorganize: bool = False,
) -> dict[str, Any]:
    """Aplica cambios masivos estilo Movie/Series Editor de *Arr."""
    movie_tmdbs: list[int] = []
    series_tmdbs: list[int] = []
    for raw in keys or []:
        k = str(raw or "").strip()
        if k.startswith("movie:tmdb:"):
            val = k.split(":")[-1]
            if val.isdigit():
                movie_tmdbs.append(int(val))
        elif k.startswith("series:tmdb:"):
            parts = k.split(":")
            if len(parts) >= 3 and parts[2].isdigit():
                series_tmdbs.append(int(parts[2]))
        elif k.startswith("series:"):
            val = k.split(":", 1)[1]
            if val.isdigit():
                series_tmdbs.append(int(val))
    movie_tmdbs = sorted(set(movie_tmdbs))
    series_tmdbs = sorted(set(series_tmdbs))

    updated_movies = 0
    updated_series = 0
    with closing(connect(db_path)) as conn:
        for tmdb in movie_tmdbs:
            sets = ["updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"]
            params: list[Any] = []
            if quality_profile_id is not ...:
                sets.append("quality_profile_id = ?")
                params.append(int(quality_profile_id) if quality_profile_id else None)
            if root_folder_id is not ...:
                sets.append("root_folder_id = ?")
                params.append(int(root_folder_id) if root_folder_id else None)
            if monitored is not None:
                sets.append("monitored = ?")
                sets.append("monitor_source = 'manual'")
                params.append(1 if monitored else 0)
            if len(sets) > 1:
                params.append(tmdb)
                cur = conn.execute(
                    f"UPDATE media_items SET {', '.join(sets)} WHERE kind = 'movie' AND tmdb = ?",
                    params,
                )
                updated_movies += cur.rowcount

        for tmdb in series_tmdbs:
            sets = ["updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"]
            params = []
            if quality_profile_id is not ...:
                sets.append("quality_profile_id = ?")
                params.append(int(quality_profile_id) if quality_profile_id else None)
            if root_folder_id is not ...:
                sets.append("root_folder_id = ?")
                params.append(int(root_folder_id) if root_folder_id else None)
            if monitored is not None and not monitor_mode:
                sets.append("monitored = ?")
                sets.append("monitor_source = 'manual'")
                params.append(1 if monitored else 0)
            if len(sets) > 1:
                params.append(tmdb)
                conn.execute(
                    f"UPDATE media_items SET {', '.join(sets)} WHERE kind = 'series' AND tmdb = ?",
                    params,
                )
            if _table_exists(conn, "series_settings"):
                existing = conn.execute(
                    "SELECT quality_profile_id, root_folder_id FROM series_settings WHERE tmdb = ?",
                    (tmdb,),
                ).fetchone()
                new_qp = (
                    (int(quality_profile_id) if quality_profile_id else None)
                    if quality_profile_id is not ...
                    else (existing["quality_profile_id"] if existing else None)
                )
                new_rf = (
                    (int(root_folder_id) if root_folder_id else None)
                    if root_folder_id is not ...
                    else (existing["root_folder_id"] if existing else None)
                )
                conn.execute(
                    """
                    INSERT INTO series_settings(tmdb, quality_profile_id, root_folder_id, updated_at)
                    VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(tmdb) DO UPDATE SET
                        quality_profile_id = excluded.quality_profile_id,
                        root_folder_id = excluded.root_folder_id,
                        updated_at = excluded.updated_at
                    """,
                    (tmdb, new_qp, new_rf),
                )
            updated_series += 1

        if _table_exists(conn, "media_tags"):
            add_ids = [int(t) for t in (add_tag_ids or []) if str(t).isdigit()]
            rem_ids = [int(t) for t in (remove_tag_ids or []) if str(t).isdigit()]
            targets = [("movie", t) for t in movie_tmdbs] + [("series", t) for t in series_tmdbs]
            for kind, tmdb in targets:
                for tid in add_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO media_tags(media_kind, tmdb, tag_id) VALUES (?, ?, ?)",
                        (kind, tmdb, tid),
                    )
                for tid in rem_ids:
                    conn.execute(
                        "DELETE FROM media_tags WHERE media_kind = ? AND tmdb = ? AND tag_id = ?",
                        (kind, tmdb, tid),
                    )
        conn.commit()

    if monitor_mode:
        for tmdb in series_tmdbs:
            apply_series_monitor_preset(db_path, tmdb, monitor_mode)

    reorg_report = None
    if reorganize and out_dir:
        moved_total = 0
        affected: list[str] = []
        for tmdb in movie_tmdbs:
            res = naming.apply_reorganize(db_path, out_dir, kind="movie", tmdb=tmdb)
            moved_total += res.get("moved", 0)
            affected.extend(res.get("affected_paths") or [])
        for tmdb in series_tmdbs:
            res = naming.apply_reorganize(db_path, out_dir, kind="series", tmdb=tmdb)
            moved_total += res.get("moved", 0)
            affected.extend(res.get("affected_paths") or [])
        reorg_report = {"moved": moved_total, "affected_paths": affected}

    return {
        "movies": len(movie_tmdbs),
        "series": updated_series,
        "updated_rows": updated_movies,
        "reorganized": reorg_report,
    }


# ═══════════════════════════════════════════════════════════════════════
# BLOCKLIST (stream_failures)
# ═══════════════════════════════════════════════════════════════════════


def list_blocklist(db_path: str | Path, *, limit: int = 200) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "stream_failures"):
            return []
        rows = conn.execute(
            """
            SELECT sf.release_id, sf.asset_id, sf.reason, sf.until, sf.created_at,
                   r.title AS release_title, r.tracker, r.quality, r.audio,
                   r.size_bytes, r.privacy,
                   m.key AS media_key, m.title AS media_title, m.kind, m.tmdb,
                   m.season, m.episode
            FROM stream_failures sf
            LEFT JOIN releases r ON r.id = sf.release_id
            LEFT JOIN media_items m ON m.id = COALESCE(sf.asset_id, r.asset_id)
            WHERE sf.until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ORDER BY sf.created_at DESC, sf.release_id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    return [dict(r) for r in rows]


def block_release(
    db_path: str | Path,
    release_id: int,
    *,
    reason: str = "manual",
    days: int = 3650,
) -> dict[str, Any]:
    release_id = int(release_id)
    reason = str(reason or "manual").strip()[:200] or "manual"
    days = max(1, min(int(days), 36500))
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id, asset_id, title, tracker FROM releases WHERE id = ?",
            (release_id,),
        ).fetchone()
        if row is None:
            raise ValueError("release no encontrado")
        conn.execute(
            """
            INSERT INTO stream_failures(release_id, asset_id, reason, until)
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?))
            ON CONFLICT(release_id) DO UPDATE SET
                reason = excluded.reason,
                until = excluded.until
            """,
            (release_id, row["asset_id"], reason, f"+{days} days"),
        )
        conn.commit()
    return {
        "release_id": release_id,
        "asset_id": row["asset_id"],
        "title": row["title"],
        "tracker": row["tracker"],
        "reason": reason,
    }


def unblock_release(db_path: str | Path, release_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM stream_failures WHERE release_id = ?", (int(release_id),)
        )
        conn.commit()
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════════════
# CUTOFF UNMET & VIRTUAL UPGRADE
# ═══════════════════════════════════════════════════════════════════════


def list_cutoff_unmet(
    db_path: str | Path,
    *,
    limit: int = 200,
    kind: str = "",
) -> dict[str, Any]:
    """Lista títulos publicados cuyo release actual no alcanza el Cutoff del perfil.

    Solo inspecciona releases ya indexados: nunca dispara descargas.
    """
    from .resolver import Resolver

    res = Resolver(db_path)
    items: list[dict[str, Any]] = []
    with closing(connect(db_path)) as conn:
        sql = """
            SELECT DISTINCT m.id, m.key, m.kind, m.tmdb, m.title, m.year,
                   m.season, m.episode, m.poster_path, m.quality_profile_id
            FROM media_items m
            WHERE m.library_added = 0
              AND EXISTS (SELECT 1 FROM releases r WHERE r.asset_id = m.id AND r.health = 'alive')
        """
        params: list[Any] = []
        if kind in ("movie", "series"):
            sql += " AND m.kind = ?"
            params.append(kind)
        sql += " ORDER BY COALESCE(m.popularity, 0) DESC, m.id DESC LIMIT 600"
        rows = conn.execute(sql, params).fetchall()

    for r in rows:
        status = res.cutoff_status(r["key"])
        if not status.get("found") or status.get("cutoff_met"):
            continue
        item = dict(r)
        item.update(
            {
                "profile_name": status.get("profile_name"),
                "cutoff_quality": status.get("cutoff_quality"),
                "current_quality": status.get("current_quality"),
                "current_cf_score": status.get("current_cf_score"),
                "current_release_id": status.get("current_release_id"),
                "best_release_id": status.get("best_release_id"),
                "best_cutoff_met": status.get("best_cutoff_met"),
                "can_upgrade_pointer": bool(
                    status.get("best_release_id")
                    and status.get("best_release_id") != status.get("current_release_id")
                ),
            }
        )
        items.append(item)
        if len(items) >= max(1, min(int(limit), 500)):
            break
    return {"items": items, "total": len(items)}


def upgrade_cutoff_pointers(
    db_path: str | Path,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """Actualiza punteros virtuales hacia el mejor release sin descargar torrent."""
    from .resolver import Resolver

    res = Resolver(db_path)
    if not keys:
        keys = [item["key"] for item in list_cutoff_unmet(db_path, limit=300)["items"]]
    upgraded: list[dict[str, Any]] = []
    for k in keys:
        st = res.cutoff_status(k)
        best_id = st.get("best_release_id")
        if not best_id:
            continue
        if best_id != st.get("current_release_id"):
            res.set_target(k, "auto", note="cutoff_virtual_upgrade")
            upgraded.append({"key": k, "best_release_id": best_id})
    return {"checked": len(keys), "upgraded": len(upgraded), "items": upgraded}


# ═══════════════════════════════════════════════════════════════════════
# IMPORT LISTS (Listas automáticas estilo Radarr/Sonarr)
# ═══════════════════════════════════════════════════════════════════════


def list_import_lists(db_path: str | Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "import_lists"):
            return []
        rows = conn.execute(
            """
            SELECT il.*,
                   qp.name AS profile_name,
                   rf.name AS root_folder_name,
                   t.name AS tag_name, t.color AS tag_color
            FROM import_lists il
            LEFT JOIN quality_profiles qp ON qp.id = il.quality_profile_id
            LEFT JOIN root_folders rf ON rf.id = il.root_folder_id
            LEFT JOIN tags t ON t.id = il.tag_id
            ORDER BY il.id DESC
            """
        ).fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"]), "publish_immediately": bool(r["publish_immediately"])} for r in rows]


def save_import_list(
    db_path: str | Path,
    data: dict[str, Any],
    *,
    list_id: int | None = None,
) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    kind = str(data.get("kind") or "tmdb_collection")
    source_ref = str(data.get("source_ref") or "").strip()
    media_kind = str(data.get("media_kind") or "movie")
    if not name or not source_ref:
        raise ValueError("nombre y referencia de origen obligatorios")
    if kind not in IMPORT_KINDS:
        raise ValueError(f"tipo de lista inválido: {kind}")
    if media_kind not in ("movie", "series"):
        raise ValueError("media_kind inválido")
    qp_id = int(data["quality_profile_id"]) if data.get("quality_profile_id") else None
    rf_id = int(data["root_folder_id"]) if data.get("root_folder_id") else None
    tag_id = int(data["tag_id"]) if data.get("tag_id") else None
    monitor_mode = str(data.get("monitor_mode") or "all")
    if monitor_mode not in MONITOR_PRESETS:
        monitor_mode = "all"
    pub = 1 if data.get("publish_immediately") else 0
    enabled = 1 if data.get("enabled", True) else 0

    with closing(connect(db_path)) as conn:
        if list_id is not None:
            conn.execute(
                """
                UPDATE import_lists SET
                    name=?, kind=?, source_ref=?, media_kind=?,
                    quality_profile_id=?, root_folder_id=?, tag_id=?,
                    monitor_mode=?, publish_immediately=?, enabled=?
                WHERE id=?
                """,
                (name, kind, source_ref, media_kind, qp_id, rf_id, tag_id, monitor_mode, pub, enabled, int(list_id)),
            )
            lid = int(list_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO import_lists(
                    name, kind, source_ref, media_kind,
                    quality_profile_id, root_folder_id, tag_id,
                    monitor_mode, publish_immediately, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, kind, source_ref, media_kind, qp_id, rf_id, tag_id, monitor_mode, pub, enabled),
            )
            lid = int(cur.lastrowid)
        conn.commit()
    return next((x for x in list_import_lists(db_path) if x["id"] == lid), {})


def delete_import_list(db_path: str | Path, list_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute("DELETE FROM import_lists WHERE id = ?", (int(list_id),))
        conn.commit()
        return cur.rowcount > 0


def _fetch_import_items(
    item: dict[str, Any],
    tmdb_client: TmdbClient | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Obtiene candidatos `(movies, series)` según el tipo de Import List."""
    kind = item["kind"]
    ref = str(item["source_ref"] or "").strip()
    media_kind = item["media_kind"]
    movies: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []

    if kind == "url":
        req = urllib.request.Request(ref, headers={"User-Agent": "TOD-ImportList/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
        entries = payload if isinstance(payload, list) else payload.get("items") or payload.get("results") or []
        for entry in entries[:100]:
            if not isinstance(entry, dict):
                continue
            tmdb_id = entry.get("tmdb_id") or entry.get("tmdb") or entry.get("id")
            if not tmdb_id:
                continue
            if entry.get("kind") == "series" or media_kind == "series":
                m = tv_item({**entry, "id": tmdb_id, "name": entry.get("title") or entry.get("name")})
                if m:
                    series.append(m)
            else:
                m = movie_item({**entry, "id": tmdb_id, "title": entry.get("title") or entry.get("name")})
                if m:
                    movies.append(m)
        return movies, series

    if tmdb_client is None:
        raise ValueError("sin API key de TMDB configurada")

    if kind == "tmdb_collection":
        col = tmdb_client.collection_details(int(ref))
        for part in col.get("parts") or []:
            m = movie_item(part)
            if m:
                movies.append(m)
    elif kind == "tmdb_list":
        lst = tmdb_client.list_details(int(ref))
        for part in lst.get("items") or []:
            if part.get("media_type") == "tv" or media_kind == "series":
                s = tv_item(part)
                if s:
                    series.append(s)
            else:
                m = movie_item(part)
                if m:
                    movies.append(m)
    elif kind == "tmdb_popular":
        pages = max(1, min(int(ref) if ref.isdigit() else 1, 3))
        for p in range(1, pages + 1):
            res = tmdb_client.popular_tv(page=p) if media_kind == "series" else tmdb_client.popular_movies(page=p)
            for part in res.get("results") or []:
                if media_kind == "series":
                    s = tv_item(part)
                    if s:
                        series.append(s)
                else:
                    m = movie_item(part)
                    if m:
                        movies.append(m)
    elif kind == "tmdb_discover":
        params = dict(urllib.parse.parse_qsl(ref)) if "=" in ref else {}
        endpoint = "discover/tv" if media_kind == "series" else "discover/movie"
        res = tmdb_client._get(endpoint, **params)
        for part in res.get("results") or []:
            if media_kind == "series":
                s = tv_item(part)
                if s:
                    series.append(s)
            else:
                m = movie_item(part)
                if m:
                    movies.append(m)
    return movies, series


def run_import_list(
    db_path: str | Path,
    config: Any,
    list_id: int,
    *,
    tmdb_client: TmdbClient | None = None,
) -> dict[str, Any]:
    lists = list_import_lists(db_path)
    spec = next((x for x in lists if x["id"] == int(list_id)), None)
    if spec is None:
        raise ValueError("lista de importación no encontrada")
    if tmdb_client is None and spec["kind"] != "url" and getattr(config, "tmdb_api_key", None):
        tmdb_client = TmdbClient.from_config(config)

    movies, series_items = _fetch_import_items(spec, tmdb_client)
    imported_movies = 0
    imported_series = 0
    keys: list[str] = []

    if movies:
        res_m = upsert_movies(db_path, movies, source="import_list", origin="import_list")
        imported_movies = int(res_m.get("inserted", 0))
        keys.extend(f"movie:tmdb:{m['tmdb']}" for m in movies)
        mass_edit_media(
            db_path,
            [f"movie:tmdb:{m['tmdb']}" for m in movies],
            quality_profile_id=spec.get("quality_profile_id") or ...,
            root_folder_id=spec.get("root_folder_id") or ...,
            monitored=spec.get("monitor_mode") != "none",
            add_tag_ids=[spec["tag_id"]] if spec.get("tag_id") else None,
        )

    for s in series_items[:30]:
        episodes: list[dict[str, Any]] = []
        if tmdb_client is not None:
            try:
                details = tmdb_client.tv_details(int(s["tmdb"]))
                for season_meta in details.get("seasons") or []:
                    snum = int(season_meta.get("season_number") or 0)
                    if snum <= 0:
                        continue
                    spayload = tmdb_client.season_details(int(s["tmdb"]), snum)
                    episodes.extend(episode_items(snum, spayload))
            except Exception:
                pass
        if episodes:
            res_s = upsert_episodes(db_path, s, episodes, source="import_list", origin="import_list")
            imported_series += 1 if res_s.get("inserted", 0) > 0 else 0
            keys.extend(f"series:tmdb:{s['tmdb']}:{e['season']}:{e['episode']}" for e in episodes)
            mass_edit_media(
                db_path,
                [f"series:{s['tmdb']}"],
                quality_profile_id=spec.get("quality_profile_id") or ...,
                root_folder_id=spec.get("root_folder_id") or ...,
                monitor_mode=spec.get("monitor_mode") or "all",
                add_tag_ids=[spec["tag_id"]] if spec.get("tag_id") else None,
            )

    status_txt = f"OK ({len(movies)} películas, {len(series_items)} series)"
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE import_lists SET last_run_at = ?, last_status = ? WHERE id = ?",
            (_now(), status_txt, int(list_id)),
        )
        conn.commit()
    return {
        "list_id": int(list_id),
        "movies_found": len(movies),
        "series_found": len(series_items),
        "inserted_movies": imported_movies,
        "inserted_series": imported_series,
        "keys": keys,
        "status": status_txt,
    }


def run_enabled_import_lists(db_path: str | Path, config: Any) -> dict[str, Any]:
    items = [x for x in list_import_lists(db_path) if x.get("enabled")]
    results = []
    errors = []
    for item in items:
        try:
            results.append(run_import_list(db_path, config, item["id"]))
        except Exception as exc:
            msg = str(exc)[:180]
            errors.append(f"{item['name']}: {msg}")
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE import_lists SET last_run_at = ?, last_status = ? WHERE id = ?",
                    (_now(), f"Error: {msg}", int(item["id"])),
                )
                conn.commit()
    return {"ran": len(items), "results": results, "errors": errors}
