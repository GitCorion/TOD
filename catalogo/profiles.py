"""Gestión de perfiles de calidad, Custom Formats, restricciones, tags y Root Folders."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .db import connect

DEFAULT_QUALITIES = ("1080p", "4K", "720p", "480p", "unknown")

DEFAULT_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "1080p Preferente (4K Fallback)",
        "is_default": 1,
        "quality_order": ["1080p", "4K", "720p", "480p", "unknown"],
        "allowed_qualities": ["1080p", "4K", "720p", "480p", "unknown"],
        "cutoff_quality": "1080p",
        "language_preference": "inherit",
        "upgrade_allowed": 1,
        "prefer_smaller": 1,
        "max_size_gb": 0.0,
        "max_mb_per_min": 0.0,
        "min_mb_per_min": 0.0,
        "block_cam": 1,
        "block_disc_images": 1,
        "min_score": -1000,
        "cutoff_score": None,
        "scores": {
            "WEB-DL": 40,
            "BluRay": 30,
            "HEVC / x265": 30,
            "PROPER / REPACK": 20,
            "Dolby Vision / HDR": 0,
            "Remux": -20,
        },
    },
    {
        "name": "4K HDR Preferente",
        "is_default": 0,
        "quality_order": ["4K", "1080p", "720p", "480p", "unknown"],
        "allowed_qualities": ["4K", "1080p", "720p"],
        "cutoff_quality": "4K",
        "language_preference": "inherit",
        "upgrade_allowed": 1,
        "prefer_smaller": 0,
        "max_size_gb": 0.0,
        "max_mb_per_min": 0.0,
        "min_mb_per_min": 0.0,
        "block_cam": 1,
        "block_disc_images": 1,
        "min_score": -1000,
        "cutoff_score": None,
        "scores": {
            "Dolby Vision / HDR": 150,
            "WEB-DL": 40,
            "BluRay": 30,
            "HEVC / x265": 20,
            "PROPER / REPACK": 20,
            "Remux": 20,
        },
    },
    {
        "name": "Streaming Ligero (Bajo Ancho de Banda)",
        "is_default": 0,
        "quality_order": ["1080p", "720p", "480p", "unknown"],
        "allowed_qualities": ["1080p", "720p", "480p"],
        "cutoff_quality": "1080p",
        "language_preference": "inherit",
        "upgrade_allowed": 1,
        "prefer_smaller": 1,
        "max_size_gb": 8.0,
        "max_mb_per_min": 90.0,
        "min_mb_per_min": 0.0,
        "block_cam": 1,
        "block_disc_images": 1,
        "min_score": -1000,
        "cutoff_score": None,
        "scores": {
            "HEVC / x265": 80,
            "WEB-DL": 40,
            "PROPER / REPACK": 20,
            "Remux": -500,
        },
    },
    {
        "name": "VOSE / Anime",
        "is_default": 0,
        "quality_order": ["1080p", "720p", "4K", "480p", "unknown"],
        "allowed_qualities": ["1080p", "720p", "4K", "480p", "unknown"],
        "cutoff_quality": "1080p",
        "language_preference": "vose_first",
        "upgrade_allowed": 1,
        "prefer_smaller": 1,
        "max_size_gb": 0.0,
        "max_mb_per_min": 0.0,
        "min_mb_per_min": 0.0,
        "block_cam": 1,
        "block_disc_images": 1,
        "min_score": -1000,
        "cutoff_score": None,
        "scores": {
            "HEVC / x265": 40,
            "WEB-DL": 40,
            "BluRay": 30,
            "PROPER / REPACK": 20,
        },
    },
)

DEFAULT_FORMATS: tuple[dict[str, Any], ...] = (
    {
        "name": "WEB-DL",
        "specs": [{"type": "source", "value": "webdl", "negate": False, "required": True}],
    },
    {
        "name": "BluRay",
        "specs": [{"type": "source", "value": "bluray", "negate": False, "required": True}],
    },
    {
        "name": "Remux",
        "specs": [{"type": "source", "value": "remux", "negate": False, "required": True}],
    },
    {
        "name": "HEVC / x265",
        "specs": [{"type": "codec", "value": "HEVC", "negate": False, "required": True}],
    },
    {
        "name": "Dolby Vision / HDR",
        "specs": [{"type": "hdr", "value": "DV|HDR10+|HDR10|HDR", "negate": False, "required": True}],
    },
    {
        "name": "PROPER / REPACK",
        "specs": [{"type": "proper", "value": "1", "negate": False, "required": True}],
    },
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def ensure_defaults(db_path: str | Path, *, catalog_library: str | Path | None = None) -> None:
    """Crea perfiles, formatos y carpetas raíz por defecto si están vacíos."""
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "quality_profiles"):
            return
        count = conn.execute("SELECT COUNT(*) FROM quality_profiles").fetchone()[0]
        if count == 0:
            fmt_ids: dict[str, int] = {}
            for fmt in DEFAULT_FORMATS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO custom_formats(name, specs_json)
                    VALUES (?, ?)
                    """,
                    (fmt["name"], json.dumps(fmt["specs"], ensure_ascii=False)),
                )
                row = conn.execute(
                    "SELECT id FROM custom_formats WHERE name=?",
                    (fmt["name"],),
                ).fetchone()
                if row:
                    fmt_ids[fmt["name"]] = int(row["id"])
            for prof in DEFAULT_PROFILES:
                cur = conn.execute(
                    """
                    INSERT INTO quality_profiles(
                        name, is_default, quality_order_json, allowed_qualities_json,
                        cutoff_quality, language_preference, upgrade_allowed,
                        prefer_smaller, max_size_gb, max_mb_per_min,
                        min_mb_per_min, block_cam, block_disc_images,
                        min_score, cutoff_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prof["name"],
                        prof["is_default"],
                        json.dumps(prof["quality_order"]),
                        json.dumps(prof["allowed_qualities"]),
                        prof["cutoff_quality"],
                        prof["language_preference"],
                        prof["upgrade_allowed"],
                        prof["prefer_smaller"],
                        prof["max_size_gb"],
                        prof["max_mb_per_min"],
                        prof["min_mb_per_min"],
                        prof["block_cam"],
                        prof["block_disc_images"],
                        prof["min_score"],
                        prof["cutoff_score"],
                    ),
                )
                pid = int(cur.lastrowid)
                for fname, score in prof.get("scores", {}).items():
                    fid = fmt_ids.get(fname)
                    if fid is not None:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO profile_format_scores(profile_id, format_id, score)
                            VALUES (?, ?, ?)
                            """,
                            (pid, fid, int(score)),
                        )
        if catalog_library and _table_exists(conn, "root_folders"):
            rcount = conn.execute("SELECT COUNT(*) FROM root_folders").fetchone()[0]
            if rcount == 0:
                base = Path(catalog_library)
                conn.execute(
                    "INSERT OR IGNORE INTO root_folders(name, kind, path, is_default) VALUES (?, 'movie', ?, 1)",
                    ("Películas", str(base / "movies")),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO root_folders(name, kind, path, is_default) VALUES (?, 'series', ?, 1)",
                    ("Series", str(base / "series")),
                )
        conn.commit()


def _hydrate_profile(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["quality_order"] = json.loads(item.pop("quality_order_json") or "[]")
    item["allowed_qualities"] = json.loads(item.pop("allowed_qualities_json") or "[]")
    item["is_default"] = bool(item["is_default"])
    item["upgrade_allowed"] = bool(item["upgrade_allowed"])
    item["prefer_smaller"] = bool(item["prefer_smaller"])
    item["block_cam"] = bool(item["block_cam"])
    item["block_disc_images"] = bool(item["block_disc_images"])
    scores = conn.execute(
        """
        SELECT pfs.format_id, cf.name, pfs.score
        FROM profile_format_scores pfs
        JOIN custom_formats cf ON cf.id = pfs.format_id
        WHERE pfs.profile_id = ?
        """,
        (item["id"],),
    ).fetchall()
    item["format_scores"] = {int(s["format_id"]): int(s["score"]) for s in scores}
    item["format_score_details"] = [dict(s) for s in scores]
    return item


def list_profiles(db_path: str | Path) -> list[dict[str, Any]]:
    ensure_defaults(db_path)
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "quality_profiles"):
            return []
        rows = conn.execute(
            "SELECT * FROM quality_profiles ORDER BY is_default DESC, id ASC"
        ).fetchall()
        return [_hydrate_profile(conn, row) for row in rows]


def get_profile(db_path: str | Path, profile_id: int | None = None) -> dict[str, Any] | None:
    ensure_defaults(db_path)
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "quality_profiles"):
            return None
        row = None
        if profile_id is not None:
            row = conn.execute(
                "SELECT * FROM quality_profiles WHERE id = ?",
                (int(profile_id),),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM quality_profiles ORDER BY is_default DESC, id ASC LIMIT 1"
            ).fetchone()
        return _hydrate_profile(conn, row) if row else None


def save_profile(db_path: str | Path, data: dict[str, Any], *, profile_id: int | None = None) -> dict[str, Any]:
    ensure_defaults(db_path)
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("el nombre del perfil es obligatorio")
    order = [str(q) for q in (data.get("quality_order") or list(DEFAULT_QUALITIES)) if str(q)]
    allowed = [str(q) for q in (data.get("allowed_qualities") or order) if str(q)]
    if not allowed:
        raise ValueError("debe permitir al menos una calidad")
    is_default = 1 if data.get("is_default") else 0
    cutoff = str(data.get("cutoff_quality") or "").strip() or None
    lang_pref = str(data.get("language_preference") or "inherit").strip()
    if lang_pref not in ("inherit", "any", "castellano_first", "only_castellano", "vose_first"):
        raise ValueError("preferencia de idioma no válida")
    with closing(connect(db_path)) as conn:
        if is_default:
            conn.execute("UPDATE quality_profiles SET is_default = 0")
        fields = (
            name,
            is_default,
            json.dumps(order),
            json.dumps(allowed),
            cutoff,
            lang_pref,
            1 if data.get("upgrade_allowed", True) else 0,
            1 if data.get("prefer_smaller", True) else 0,
            max(0.0, float(data.get("max_size_gb") or 0.0)),
            max(0.0, float(data.get("max_mb_per_min") or 0.0)),
            max(0.0, float(data.get("min_mb_per_min") or 0.0)),
            1 if data.get("block_cam", True) else 0,
            1 if data.get("block_disc_images", True) else 0,
            int(data.get("min_score") or 0),
            int(data["cutoff_score"]) if data.get("cutoff_score") not in (None, "") else None,
            int(data["radarr_profile_id"]) if data.get("radarr_profile_id") not in (None, "") else None,
            int(data["sonarr_profile_id"]) if data.get("sonarr_profile_id") not in (None, "") else None,
        )
        if profile_id is not None:
            conn.execute(
                """
                UPDATE quality_profiles SET
                    name=?, is_default=?, quality_order_json=?, allowed_qualities_json=?,
                    cutoff_quality=?, language_preference=?, upgrade_allowed=?,
                    prefer_smaller=?, max_size_gb=?, max_mb_per_min=?,
                    min_mb_per_min=?, block_cam=?, block_disc_images=?,
                    min_score=?, cutoff_score=?, radarr_profile_id=?,
                    sonarr_profile_id=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id=?
                """,
                fields + (int(profile_id),),
            )
            pid = int(profile_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO quality_profiles(
                    name, is_default, quality_order_json, allowed_qualities_json,
                    cutoff_quality, language_preference, upgrade_allowed,
                    prefer_smaller, max_size_gb, max_mb_per_min,
                    min_mb_per_min, block_cam, block_disc_images,
                    min_score, cutoff_score, radarr_profile_id, sonarr_profile_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                fields,
            )
            pid = int(cur.lastrowid)
        if isinstance(data.get("format_scores"), dict):
            conn.execute("DELETE FROM profile_format_scores WHERE profile_id = ?", (pid,))
            for fid_raw, score in data["format_scores"].items():
                conn.execute(
                    "INSERT INTO profile_format_scores(profile_id, format_id, score) VALUES (?, ?, ?)",
                    (pid, int(fid_raw), int(score or 0)),
                )
        conn.commit()
    return get_profile(db_path, pid) or {}


def delete_profile(db_path: str | Path, profile_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT is_default FROM quality_profiles WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
        if row is None:
            return False
        if row["is_default"]:
            raise ValueError("no se puede borrar el perfil por defecto")
        conn.execute(
            "UPDATE media_items SET quality_profile_id = NULL WHERE quality_profile_id = ?",
            (int(profile_id),),
        )
        conn.execute(
            "UPDATE series_settings SET quality_profile_id = NULL WHERE quality_profile_id = ?",
            (int(profile_id),),
        )
        cur = conn.execute(
            "DELETE FROM quality_profiles WHERE id = ?",
            (int(profile_id),),
        )
        conn.commit()
        return cur.rowcount > 0


def list_custom_formats(db_path: str | Path) -> list[dict[str, Any]]:
    ensure_defaults(db_path)
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "custom_formats"):
            return []
        rows = conn.execute("SELECT * FROM custom_formats ORDER BY id ASC").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["specs"] = json.loads(item.pop("specs_json") or "[]")
        item["include_when_renaming"] = bool(item["include_when_renaming"])
        item["enabled"] = bool(item["enabled"])
        out.append(item)
    return out


def save_custom_format(
    db_path: str | Path, data: dict[str, Any], *, format_id: int | None = None
) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("el nombre del formato es obligatorio")
    specs = data.get("specs") or []
    if not isinstance(specs, list) or not specs:
        raise ValueError("el formato necesita al menos una regla")
    with closing(connect(db_path)) as conn:
        if format_id is not None:
            conn.execute(
                """
                UPDATE custom_formats SET
                    name=?, include_when_renaming=?, specs_json=?, enabled=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id=?
                """,
                (
                    name,
                    1 if data.get("include_when_renaming") else 0,
                    json.dumps(specs, ensure_ascii=False),
                    1 if data.get("enabled", True) else 0,
                    int(format_id),
                ),
            )
            fid = int(format_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO custom_formats(name, include_when_renaming, specs_json, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (
                    name,
                    1 if data.get("include_when_renaming") else 0,
                    json.dumps(specs, ensure_ascii=False),
                    1 if data.get("enabled", True) else 0,
                ),
            )
            fid = int(cur.lastrowid)
        conn.commit()
    return next((f for f in list_custom_formats(db_path) if f["id"] == fid), {})


def delete_custom_format(db_path: str | Path, format_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute("DELETE FROM custom_formats WHERE id = ?", (int(format_id),))
        conn.commit()
        return cur.rowcount > 0


def list_restrictions(db_path: str | Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        if not _table_exists(conn, "release_restrictions"):
            return []
        rows = conn.execute(
            """
            SELECT rr.*, t.name AS tag_name
            FROM release_restrictions rr
            LEFT JOIN tags t ON t.id = rr.tag_id
            ORDER BY rr.id ASC
            """
        ).fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def save_restriction(
    db_path: str | Path, data: dict[str, Any], *, restriction_id: int | None = None
) -> dict[str, Any]:
    name = str(data.get("name") or "").strip() or "Regla"
    must = str(data.get("must_contain") or "").strip()
    must_not = str(data.get("must_not_contain") or "").strip()
    tag_id = int(data["tag_id"]) if data.get("tag_id") not in (None, "") else None
    enabled = 1 if data.get("enabled", True) else 0
    with closing(connect(db_path)) as conn:
        if restriction_id is not None:
            conn.execute(
                """
                UPDATE release_restrictions SET name=?, must_contain=?, must_not_contain=?,
                    tag_id=?, enabled=? WHERE id=?
                """,
                (name, must, must_not, tag_id, enabled, int(restriction_id)),
            )
            rid = int(restriction_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO release_restrictions(name, must_contain, must_not_contain, tag_id, enabled)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, must, must_not, tag_id, enabled),
            )
            rid = int(cur.lastrowid)
        conn.commit()
    return next((r for r in list_restrictions(db_path) if r["id"] == rid), {})


def delete_restriction(db_path: str | Path, restriction_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM release_restrictions WHERE id = ?", (int(restriction_id),)
        )
        conn.commit()
        return cur.rowcount > 0
