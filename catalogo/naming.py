"""Organización de archivos .strm al estilo Media Management de Sonarr/Radarr.

Controla carpetas raíz (Root Folders), separación opcional de Anime,
plantillas de nombrado configurables con vista previa y reorganización segura
de archivos .strm ya publicados sin tocar torrents ni descargas.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import urllib.parse
from contextlib import closing
from pathlib import Path
from typing import Any

from . import settings
from .db import connect

DEFAULTS = {
    "naming.movie_folder": "{Movie Title} ({Release Year})",
    "naming.movie_file": "{Movie Title} ({Release Year})",
    "naming.series_folder": "{Series Title} ({Year})",
    "naming.season_folder": "Season {season:00}",
    "naming.episode_file": "{Series Title} S{season:00}E{episode:00}",
    "naming.use_season_folders": "1",
    "naming.version_suffix": "{Quality Title} {Audio Language}",
    "naming.separate_anime": "0",
    "naming.anime_series_folder": "anime",
    "naming.anime_movies_folder": "movies",
    "naming.rich_nfo": "1",
}

INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
EMPTY_PARENS = re.compile(r"\(\s*\)|\[\s*\]")
MULTI_SPACE = re.compile(r"\s{2,}")


def safe_segment(value: str | None, fallback: str = "Sin titulo") -> str:
    text = INVALID_CHARS.sub(" ", value or "").strip()
    text = EMPTY_PARENS.sub("", text)
    text = MULTI_SPACE.sub(" ", text).strip(" ._-")
    return text[:180] or fallback


def load_naming_settings(db_path: str | Path) -> dict[str, Any]:
    raw: dict[str, str] = {}
    with closing(connect(db_path)) as conn:
        for key, default in DEFAULTS.items():
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            raw[key] = str(row["value"]) if row is not None and row["value"] is not None else default
    return {
        "movie_folder": raw["naming.movie_folder"],
        "movie_file": raw["naming.movie_file"],
        "series_folder": raw["naming.series_folder"],
        "season_folder": raw["naming.season_folder"],
        "episode_file": raw["naming.episode_file"],
        "use_season_folders": raw["naming.use_season_folders"] in ("1", "true", "yes"),
        "version_suffix": raw["naming.version_suffix"],
        "separate_anime": raw["naming.separate_anime"] in ("1", "true", "yes"),
        "anime_series_folder": raw["naming.anime_series_folder"] or "anime",
        "anime_movies_folder": raw["naming.anime_movies_folder"] or "movies",
        "rich_nfo": raw["naming.rich_nfo"] in ("1", "true", "yes"),
    }


def save_naming_settings(db_path: str | Path, values: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "movie_folder": ("naming.movie_folder", "str"),
        "movie_file": ("naming.movie_file", "str"),
        "series_folder": ("naming.series_folder", "str"),
        "season_folder": ("naming.season_folder", "str"),
        "episode_file": ("naming.episode_file", "str"),
        "use_season_folders": ("naming.use_season_folders", "bool"),
        "version_suffix": ("naming.version_suffix", "str"),
        "separate_anime": ("naming.separate_anime", "bool"),
        "anime_series_folder": ("naming.anime_series_folder", "segment"),
        "anime_movies_folder": ("naming.anime_movies_folder", "segment"),
        "rich_nfo": ("naming.rich_nfo", "bool"),
    }
    for field, (key, kind) in mapping.items():
        if field not in values:
            continue
        val = values[field]
        if kind == "bool":
            stored = "1" if val in (True, 1, "1", "true", "yes") else "0"
        elif kind == "segment":
            stored = safe_segment(str(val or ""), DEFAULTS[key])
        else:
            stored = str(val or "").strip() or DEFAULTS[key]
        settings.set_setting(db_path, key, stored)
    return load_naming_settings(db_path)


def _render_template(template: str, tokens: dict[str, Any], fallback: str) -> str:
    text = template
    for key, val in tokens.items():
        text = text.replace(f"{{{key}}}", "" if val is None else str(val))
    # Clean leftover unknown tokens safely
    text = re.sub(r"\{[^}]+\}", "", text)
    return safe_segment(text, fallback)


def _is_anime_asset(asset: dict[str, Any]) -> bool:
    if asset.get("is_anime"):
        return True
    if asset.get("series_type") == "anime":
        return True
    orig_lang = str(asset.get("original_language") or "").lower()
    countries = str(asset.get("original_country") or "").upper()
    genres = str(asset.get("genres") or "").lower()
    return (orig_lang == "ja" or "JP" in countries.split(",")) and (
        "anim" in genres or orig_lang == "ja"
    )


def list_root_folders(db_path: str | Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='root_folders'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT * FROM root_folders ORDER BY is_default DESC, id ASC"
        ).fetchall()
    return [{**dict(r), "is_default": bool(r["is_default"])} for r in rows]


def save_root_folder(
    db_path: str | Path,
    *,
    name: str,
    kind: str,
    path: str,
    is_default: bool = False,
    folder_id: int | None = None,
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_path = str(path or "").strip()
    if not clean_name or not clean_path:
        raise ValueError("nombre y ruta obligatorios")
    if kind not in ("movie", "series", "anime", "any"):
        raise ValueError("tipo de carpeta inválido")
    with closing(connect(db_path)) as conn:
        if is_default:
            conn.execute(
                "UPDATE root_folders SET is_default = 0 WHERE kind = ?", (kind,)
            )
        if folder_id is not None:
            conn.execute(
                "UPDATE root_folders SET name=?, kind=?, path=?, is_default=? WHERE id=?",
                (clean_name, kind, clean_path, 1 if is_default else 0, int(folder_id)),
            )
            fid = int(folder_id)
        else:
            cur = conn.execute(
                "INSERT INTO root_folders(name, kind, path, is_default) VALUES (?, ?, ?, ?)",
                (clean_name, kind, clean_path, 1 if is_default else 0),
            )
            fid = int(cur.lastrowid)
        conn.commit()
    return next((r for r in list_root_folders(db_path) if r["id"] == fid), {})


def delete_root_folder(db_path: str | Path, folder_id: int) -> bool:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT is_default FROM root_folders WHERE id=?", (int(folder_id),)
        ).fetchone()
        if row is None:
            return False
        if row["is_default"]:
            raise ValueError("no se puede borrar una carpeta raíz por defecto")
        conn.execute(
            "UPDATE media_items SET root_folder_id = NULL WHERE root_folder_id = ?",
            (int(folder_id),),
        )
        conn.execute(
            "UPDATE series_settings SET root_folder_id = NULL WHERE root_folder_id = ?",
            (int(folder_id),),
        )
        cur = conn.execute("DELETE FROM root_folders WHERE id = ?", (int(folder_id),))
        conn.commit()
        return cur.rowcount > 0


def resolve_base_dir(
    out_dir: Path,
    asset: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    root_folders_by_id: dict[int, dict[str, Any]] | None = None,
    series_settings_map: dict[int, dict[str, Any]] | None = None,
) -> Path:
    rf_id = asset.get("root_folder_id")
    if rf_id is None and asset.get("kind") == "series" and asset.get("tmdb") and series_settings_map:
        ss = series_settings_map.get(int(asset["tmdb"]))
        if ss:
            rf_id = ss.get("root_folder_id")
    if rf_id and root_folders_by_id and int(rf_id) in root_folders_by_id:
        rf_path = Path(root_folders_by_id[int(rf_id)]["path"])
        return rf_path
    if cfg and cfg.get("separate_anime") and _is_anime_asset(asset):
        if asset.get("kind") == "series":
            return out_dir / str(cfg.get("anime_series_folder") or "anime")
        sub = str(cfg.get("anime_movies_folder") or "movies")
        return out_dir / sub
    return out_dir / ("movies" if asset.get("kind") == "movie" else "series")


def build_asset_strm_path(
    out_dir: Path,
    asset: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    root_folders_by_id: dict[int, dict[str, Any]] | None = None,
    series_settings_map: dict[int, dict[str, Any]] | None = None,
) -> Path:
    title = safe_segment(asset.get("title") or asset.get("key"))
    orig = safe_segment(asset.get("original_title") or title, title)
    year = asset.get("year")
    tmdb = asset.get("tmdb")
    imdb = asset.get("imdb_id") or asset.get("imdb") or ""
    base_dir = resolve_base_dir(
        out_dir,
        asset,
        cfg=cfg,
        root_folders_by_id=root_folders_by_id,
        series_settings_map=series_settings_map,
    )
    if not cfg:
        suffix = f" ({year})" if year else ""
        if asset.get("kind") == "movie":
            return base_dir / f"{title}{suffix}" / f"{title}{suffix}.strm"
        s = int(asset.get("season") or 0)
        e = int(asset.get("episode") or 0)
        return (
            base_dir
            / f"{title}{suffix}"
            / f"Season {s:02d}"
            / f"{title} S{s:02d}E{e:02d}.strm"
        )

    if asset.get("kind") == "movie":
        tokens = {
            "Movie Title": title,
            "Original Title": orig,
            "Release Year": str(year) if year else "",
            "Year": str(year) if year else "",
            "TmdbId": str(tmdb or ""),
            "ImdbId": str(imdb),
            "tmdb-id": f"tmdbid-{tmdb}" if tmdb else "",
            "imdb-id": f"imdbid-{imdb}" if imdb else "",
        }
        folder = _render_template(cfg["movie_folder"], tokens, title)
        fname = _render_template(cfg["movie_file"], tokens, title)
        return base_dir / folder / f"{fname}.strm"

    s = int(asset.get("season") or 0)
    e = int(asset.get("episode") or 0)
    ep_title = safe_segment(asset.get("episode_title") or "", "")
    tokens = {
        "Series Title": title,
        "Original Title": orig,
        "Year": str(year) if year else "",
        "Release Year": str(year) if year else "",
        "TmdbId": str(tmdb or ""),
        "TvdbId": str(asset.get("tvdb_id") or ""),
        "season:00": f"{s:02d}",
        "season": str(s),
        "episode:00": f"{e:02d}",
        "episode": str(e),
        "Episode Title": ep_title,
    }
    series_folder = _render_template(cfg["series_folder"], tokens, title)
    ep_file = _render_template(cfg["episode_file"], tokens, f"{title} S{s:02d}E{e:02d}")
    if cfg.get("use_season_folders", True):
        season_folder = _render_template(cfg["season_folder"], tokens, f"Season {s:02d}")
        return base_dir / series_folder / season_folder / f"{ep_file}.strm"
    return base_dir / series_folder / f"{ep_file}.strm"


def preview_examples(cfg: dict[str, Any], out_dir: Path | str = "/mnt/catalogo") -> dict[str, str]:
    root = Path(out_dir)
    movie_sample = {
        "kind": "movie",
        "key": "movie:tmdb:157336",
        "tmdb": 157336,
        "imdb_id": "tt0816692",
        "title": "Interstellar",
        "original_title": "Interstellar",
        "year": 2014,
    }
    series_sample = {
        "kind": "series",
        "key": "series:tmdb:1399:1:3",
        "tmdb": 1399,
        "title": "Juego de Tronos",
        "original_title": "Game of Thrones",
        "year": 2011,
        "season": 1,
        "episode": 3,
        "episode_title": "Lord Nieve",
    }
    anime_sample = {
        "kind": "series",
        "key": "series:tmdb:37854:1:1",
        "tmdb": 37854,
        "title": "One Piece",
        "original_title": "ONE PIECE",
        "year": 1999,
        "season": 1,
        "episode": 1,
        "episode_title": "Romance Dawn",
        "is_anime": 1,
    }
    return {
        "movie": str(build_asset_strm_path(root, movie_sample, cfg=cfg)),
        "series": str(build_asset_strm_path(root, series_sample, cfg=cfg)),
        "anime": str(build_asset_strm_path(root, anime_sample, cfg=cfg)),
    }


def plan_reorganize(
    db_path: str | Path,
    out_dir: Path | str,
    *,
    kind: str | None = None,
    tmdb: int | None = None,
) -> list[dict[str, str]]:
    """Calcula qué .strm cambiarían de ruta según Root Folders/Anime/Naming."""
    root = Path(out_dir)
    if not root.is_dir():
        return []
    cfg = load_naming_settings(db_path)
    with closing(connect(db_path)) as conn:
        folders = {rf["id"]: rf for rf in list_root_folders(db_path)}
        ss_map = {}
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_settings'"
        ).fetchone():
            for r in conn.execute("SELECT * FROM series_settings").fetchall():
                ss_map[int(r["tmdb"])] = dict(r)
        sql = "SELECT * FROM media_items WHERE 1=1"
        params: list[Any] = []
        if kind in ("movie", "series"):
            sql += " AND kind = ?"
            params.append(kind)
        if tmdb:
            sql += " AND tmdb = ?"
            params.append(int(tmdb))
        assets = {row["key"]: dict(row) for row in conn.execute(sql, params).fetchall()}

    moves: list[dict[str, str]] = []
    scan_dirs: set[Path] = {root}
    for rf in folders.values():
        p = Path(rf["path"])
        if p.is_dir():
            scan_dirs.add(p)

    seen_files: set[Path] = set()
    for base in scan_dirs:
        for path in base.rglob("*.strm"):
            if path in seen_files:
                continue
            seen_files.add(path)
            try:
                content = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(content).query)
            key = (qs.get("key") or [None])[0]
            if not key or key not in assets:
                continue
            asset = assets[key]
            target_default = build_asset_strm_path(
                root,
                asset,
                cfg=cfg,
                root_folders_by_id=folders,
                series_settings_map=ss_map,
            )
            release_id = (qs.get("release") or [None])[0]
            if release_id is None and target_default != path:
                moves.append(
                    {
                        "key": key,
                        "title": asset.get("title") or key,
                        "old_path": str(path),
                        "new_path": str(target_default),
                    }
                )
            elif release_id is not None and path.parent != target_default.parent:
                # Preserve existing version suffix when moving folders
                suffix = path.name
                old_stem = path.stem
                if " - " in old_stem:
                    ver = old_stem.split(" - ", 1)[1]
                    suffix = f"{target_default.stem} - {ver}.strm"
                new_ver_path = target_default.parent / suffix
                if new_ver_path != path:
                    moves.append(
                        {
                            "key": key,
                            "title": asset.get("title") or key,
                            "old_path": str(path),
                            "new_path": str(new_ver_path),
                        }
                    )
    return moves


def apply_reorganize(
    db_path: str | Path,
    out_dir: Path | str,
    *,
    kind: str | None = None,
    tmdb: int | None = None,
) -> dict[str, Any]:
    """Ejecuta los movimientos de .strm y limpia directorios vacíos."""
    root = Path(out_dir)
    moves = plan_reorganize(db_path, root, kind=kind, tmdb=tmdb)
    moved = 0
    errors: list[str] = []
    affected_paths: list[Path] = []
    for item in moves:
        src = Path(item["old_path"])
        dst = Path(item["new_path"])
        if not src.exists():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
            affected_paths.append(dst)
            parent = src.parent
            while parent != root and parent != parent.parent and parent.is_dir():
                if any(parent.iterdir()):
                    break
                parent.rmdir()
                parent = parent.parent
        except OSError as exc:
            errors.append(f"{src}: {exc}")
    return {
        "planned": len(moves),
        "moved": moved,
        "errors": errors,
        "affected_paths": [str(p) for p in affected_paths],
    }
