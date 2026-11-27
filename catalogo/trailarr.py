"""Integración con Trailarr y puente Radarr/Sonarr v3 hacia carpetas .strm.

TOD expone endpoints compatibles con la API v3 de Radarr y Sonarr
(`/trailarr/radarr/api/v3/...` y `/trailarr/sonarr/api/v3/...`) devolviendo
las rutas exactas de las carpetas `.strm` generadas por `naming.py`. De este
modo Trailarr descarga los trailers directamente dentro de las carpetas de TOD,
sin desajustes por idioma en Sonarr ni por variantes de traducción en Radarr.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

from . import naming as naming_mod
from .config import Config
from .db import connect


class TrailarrError(RuntimeError):
    pass


class TrailarrClient:
    """Cliente HTTP mínimo para la API v1 de Trailarr."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        timeout: float = 20.0,
    ):
        if not api_key:
            raise TrailarrError("sin API key de Trailarr")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_config(
        cls, config: Config, *, timeout: float = 20.0
    ) -> "TrailarrClient":
        return cls(
            config.trailarr_url,
            config.trailarr_api_key,
            timeout=timeout,
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "tod-trailarr/0.1",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise TrailarrError(f"HTTP {exc.code} en {path}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TrailarrError(f"no responde: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TrailarrError("respuesta JSON inválida") from exc

    def status(self) -> dict[str, Any]:
        """Consulta ajustes o estado de Trailarr para validar conexión."""
        for path in ("/api/v1/settings/", "/api/v1/system/status"):
            try:
                data = self._request(path)
                if isinstance(data, dict):
                    return data
            except TrailarrError as exc:
                if "HTTP 404" in str(exc):
                    continue
                raise
        raise TrailarrError("Trailarr no expone /api/v1/settings/")

    def connections(self) -> list[dict[str, Any]]:
        data = self._request("/api/v1/connections/")
        return data if isinstance(data, list) else []

    def media_all(self) -> list[dict[str, Any]]:
        data = self._request("/api/v1/media/all")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "media", "results"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
        return []

    def tasks(self) -> list[dict[str, Any]]:
        data = self._request("/api/v1/tasks/")
        return data if isinstance(data, list) else []

    def run_task(self, task_id: str | int) -> Any:
        return self._request(f"/api/v1/tasks/run/{task_id}", method="GET")


def _naming_context(db_path: str | Path) -> tuple[dict[str, Any], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    cfg = naming_mod.load_naming_settings(db_path)
    rf_map = {rf["id"]: rf for rf in naming_mod.list_root_folders(db_path)}
    ss_map: dict[int, dict[str, Any]] = {}
    with closing(connect(db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_settings'"
        ).fetchone()
        if exists:
            for row in conn.execute("SELECT * FROM series_settings").fetchall():
                ss_map[int(row["tmdb"])] = dict(row)
    return cfg, rf_map, ss_map


def _images(poster_path: str | None, backdrop_path: str | None = None) -> list[dict[str, str]]:
    imgs: list[dict[str, str]] = []
    if poster_path:
        url = (
            poster_path
            if str(poster_path).startswith(("http://", "https://"))
            else f"https://image.tmdb.org/t/p/original{poster_path}"
        )
        imgs.append({"coverType": "poster", "remoteUrl": url})
    if backdrop_path:
        url = (
            backdrop_path
            if str(backdrop_path).startswith(("http://", "https://"))
            else f"https://image.tmdb.org/t/p/original{backdrop_path}"
        )
        imgs.append({"coverType": "fanart", "remoteUrl": url})
    return imgs


def radarr_system_status() -> dict[str, Any]:
    return {
        "appName": "Radarr",
        "instanceName": "TOD-Radarr",
        "version": "5.0.0.0",
        "isLinux": True,
        "isProduction": True,
        "authentication": "none",
        "urlBase": "",
    }


def sonarr_system_status() -> dict[str, Any]:
    return {
        "appName": "Sonarr",
        "instanceName": "TOD-Sonarr",
        "version": "4.0.0.700",
        "isLinux": True,
        "isProduction": True,
        "authentication": "none",
        "urlBase": "",
    }


def radarr_root_folders(db_path: str | Path, config: Config) -> list[dict[str, Any]]:
    cfg, rf_map, _ = _naming_context(db_path)
    out_dir = Path(config.catalog_library)
    paths: list[str] = [str(out_dir / "movies")]
    if cfg.get("separate_anime"):
        anime_dir = str(out_dir / str(cfg.get("anime_movies_folder") or "movies"))
        if anime_dir not in paths:
            paths.append(anime_dir)
    for rf in rf_map.values():
        if rf.get("kind") in ("movie", "anime", "any") and rf.get("path"):
            p = str(rf["path"])
            if p not in paths:
                paths.append(p)
    return [
        {
            "id": idx + 1,
            "path": path,
            "accessible": True,
            "freeSpace": 0,
            "unmappedFolders": [],
        }
        for idx, path in enumerate(paths)
    ]


def sonarr_root_folders(db_path: str | Path, config: Config) -> list[dict[str, Any]]:
    cfg, rf_map, _ = _naming_context(db_path)
    out_dir = Path(config.catalog_library)
    paths: list[str] = [str(out_dir / "series")]
    if cfg.get("separate_anime"):
        anime_dir = str(out_dir / str(cfg.get("anime_series_folder") or "anime"))
        if anime_dir not in paths:
            paths.append(anime_dir)
    for rf in rf_map.values():
        if rf.get("kind") in ("series", "anime", "any") and rf.get("path"):
            p = str(rf["path"])
            if p not in paths:
                paths.append(p)
    return [
        {
            "id": idx + 1,
            "path": path,
            "accessible": True,
            "freeSpace": 0,
            "unmappedFolders": [],
        }
        for idx, path in enumerate(paths)
    ]


def radarr_movies(db_path: str | Path, config: Config) -> list[dict[str, Any]]:
    """Devuelve películas de TOD en formato Radarr v3 con la ruta .strm real."""
    cfg, rf_map, _ = _naming_context(db_path)
    out_dir = Path(config.catalog_library)
    check_disk = out_dir.is_dir()
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM media_items WHERE kind = 'movie' AND library_added = 1 ORDER BY id ASC"
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        asset = dict(row)
        tmdb = int(asset.get("tmdb") or 0)
        if not tmdb:
            continue
        strm_file = naming_mod.build_asset_strm_path(
            out_dir, asset, cfg=cfg, root_folders_by_id=rf_map
        )
        movie_dir = strm_file.parent
        if check_disk and not movie_dir.is_dir():
            continue
        title = str(asset.get("title") or "").strip()
        year = int(asset.get("year") or 0)
        items.append(
            {
                "id": int(asset["id"]),
                "title": title,
                "originalTitle": asset.get("original_title") or title,
                "sortTitle": title.lower(),
                "year": year,
                "tmdbId": tmdb,
                "imdbId": asset.get("imdb_id") or "",
                "overview": asset.get("overview") or "",
                "monitored": True,
                "hasFile": True,
                "isAvailable": True,
                "path": str(movie_dir),
                "folderName": movie_dir.name,
                "rootFolderPath": str(movie_dir.parent),
                "youTubeTrailerId": "",
                "images": _images(
                    asset.get("poster_path"), asset.get("backdrop_path")
                ),
            }
        )
    return items


def sonarr_series(db_path: str | Path, config: Config) -> list[dict[str, Any]]:
    """Devuelve series de TOD en formato Sonarr v3 con la ruta .strm real."""
    cfg, rf_map, ss_map = _naming_context(db_path)
    out_dir = Path(config.catalog_library)
    check_disk = out_dir.is_dir()
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM media_items WHERE kind = 'series' AND tmdb IS NOT NULL AND library_added = 1 ORDER BY id ASC"
        ).fetchall()

    seen: dict[int, dict[str, Any]] = {}
    for row in rows:
        asset = dict(row)
        tmdb = int(asset.get("tmdb") or 0)
        if not tmdb or tmdb in seen:
            continue
        seen[tmdb] = asset

    use_season_folders = cfg.get("use_season_folders", True)
    items: list[dict[str, Any]] = []
    for tmdb, asset in seen.items():
        strm_file = naming_mod.build_asset_strm_path(
            out_dir,
            asset,
            cfg=cfg,
            root_folders_by_id=rf_map,
            series_settings_map=ss_map,
        )
        series_dir = strm_file.parent.parent if use_season_folders else strm_file.parent
        if check_disk and not series_dir.is_dir():
            continue
        title = str(asset.get("title") or "").strip()
        year = int(asset.get("year") or 0)
        items.append(
            {
                "id": tmdb,
                "title": title,
                "sortTitle": title.lower(),
                "year": year,
                "tmdbId": tmdb,
                "tvdbId": int(asset.get("tvdb_id") or 0),
                "imdbId": asset.get("imdb_id") or "",
                "overview": asset.get("overview") or "",
                "monitored": True,
                "seriesType": asset.get("series_type") or "standard",
                "path": str(series_dir),
                "rootFolderPath": str(series_dir.parent),
                "seasonFolder": bool(use_season_folders),
                "images": _images(
                    asset.get("poster_path"), asset.get("backdrop_path")
                ),
            }
        )
    return items


def sync_trailarr(config: Config) -> dict[str, Any]:
    """Solicita a Trailarr refrescar conexiones o buscar trailers pendientes."""
    client = TrailarrClient.from_config(config)
    connections = client.connections()
    tasks = client.tasks()
    triggered: list[str] = []
    for task in tasks:
        task_id = task.get("id") or task.get("task_id")
        name = str(task.get("name") or task.get("task_name") or "").lower()
        if task_id is not None and any(
            kw in name for kw in ("refresh", "scan", "sync", "trailer")
        ):
            try:
                client.run_task(task_id)
                triggered.append(str(task.get("name") or task_id))
            except TrailarrError:
                continue
    return {
        "ok": True,
        "connections": len(connections),
        "tasks_found": len(tasks),
        "triggered": triggered,
    }
