"""Datos de biblioteca de Radarr y Sonarr, en solo lectura.

No se crean ni modifican películas o series aquí. Solo se consultan sus APIs
para saber qué existe de verdad y qué está en cola.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .arr import RadarrClient, SonarrClient

DEFAULT_TTL = 60.0


def _pick_disk(records: list[dict]) -> dict[str, Any] | None:
    if not records:
        return None
    for preferred in ("/media", "/mnt", "/"):
        for row in records:
            if (row.get("path") or "") == preferred:
                return row
    for row in records:
        if int(row.get("totalSpace") or 0) > 0:
            return row
    return records[0]


def _stat(series: dict, key: str) -> int:
    stats = series.get("statistics") or {}
    try:
        return int(stats.get(key) or 0)
    except (TypeError, ValueError):
        return 0


class ArrData:
    def __init__(self, config: Any, *, ttl: float = DEFAULT_TTL):
        self.config = config
        self.ttl = ttl
        self._lock = threading.Lock()
        self._overview: dict[str, Any] | None = None
        self._overview_at = 0.0
        self._episodes: dict[int, tuple[float, list[dict]]] = {}

    def invalidate(self) -> None:
        with self._lock:
            self._overview = None
            self._overview_at = 0.0
            self._episodes.clear()

    def _radarr(self) -> RadarrClient:
        return RadarrClient.from_config(self.config)

    def _sonarr(self) -> SonarrClient:
        return SonarrClient.from_config(self.config)

    # ------------------------------------------------------------- overview

    def overview(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._overview is not None
                and now - self._overview_at < self.ttl
            ):
                return self._overview
        data = self._load()
        with self._lock:
            self._overview = data
            self._overview_at = now
        return data

    def _load(self) -> dict[str, Any]:
        errors: list[str] = []
        movies: list[dict] = []
        series: list[dict] = []
        radarr_queue: dict[str, Any] = {}
        sonarr_queue: dict[str, Any] = {}
        radarr_disks: list[dict] = []
        sonarr_disks: list[dict] = []
        radarr_status: dict[str, Any] = {}
        sonarr_status: dict[str, Any] = {}
        radarr_history: list[dict] = []
        sonarr_history: list[dict] = []

        def fetch(name: str, fn):
            try:
                return fn()
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:160]}")
                return None

        try:
            radarr = self._radarr()
            sonarr = self._sonarr()
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc)[:200],
                "fetched_at": time.time(),
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = {
                "movies": pool.submit(fetch, "Radarr películas", radarr.movies),
                "series": pool.submit(fetch, "Sonarr series", sonarr.series),
                "radarr_queue": pool.submit(
                    fetch, "Radarr cola", radarr.queue
                ),
                "sonarr_queue": pool.submit(
                    fetch, "Sonarr cola", sonarr.queue
                ),
                "radarr_disks": pool.submit(
                    fetch, "Radarr discos", radarr.diskspace
                ),
                "sonarr_disks": pool.submit(
                    fetch, "Sonarr discos", sonarr.diskspace
                ),
                "radarr_status": pool.submit(
                    fetch, "Radarr estado", radarr.status
                ),
                "sonarr_status": pool.submit(
                    fetch, "Sonarr estado", sonarr.status
                ),
                "radarr_history": pool.submit(
                    fetch, "Radarr historial", lambda: radarr.history(page_size=8)
                ),
                "sonarr_history": pool.submit(
                    fetch, "Sonarr historial", lambda: sonarr.history(page_size=8)
                ),
            }
            results = {key: job.result() for key, job in jobs.items()}

        movies = results["movies"] or []
        series = results["series"] or []
        radarr_queue = results["radarr_queue"] or {}
        sonarr_queue = results["sonarr_queue"] or {}
        radarr_disks = results["radarr_disks"] or []
        sonarr_disks = results["sonarr_disks"] or []
        radarr_status = results["radarr_status"] or {}
        sonarr_status = results["sonarr_status"] or {}
        radarr_history = results["radarr_history"] or []
        sonarr_history = results["sonarr_history"] or []

        movies_with_file = [row for row in movies if row.get("hasFile")]
        movies_by_tmdb: dict[int, dict] = {}
        for row in movies:
            try:
                tmdb = int(row.get("tmdbId") or 0)
            except (TypeError, ValueError):
                tmdb = 0
            if tmdb:
                movies_by_tmdb[tmdb] = row

        series_by_tmdb: dict[int, dict] = {}
        for row in series:
            try:
                tmdb = int(row.get("tmdbId") or 0)
            except (TypeError, ValueError):
                tmdb = 0
            if tmdb:
                series_by_tmdb[tmdb] = row

        episodes_available = sum(_stat(row, "episodeFileCount") for row in series)
        episodes_total = sum(_stat(row, "totalEpisodeCount") for row in series)
        series_available = sum(
            1 for row in series if _stat(row, "episodeFileCount") > 0
        )

        recent: list[dict] = []
        for row in movies_with_file:
            movie_file = row.get("movieFile") or {}
            date = movie_file.get("dateAdded")
            if not date:
                continue
            recent.append(
                {
                    "kind": "movie",
                    "title": row.get("title"),
                    "year": row.get("year"),
                    "date": date,
                    "tmdb": row.get("tmdbId"),
                }
            )
        for row in sonarr_history:
            series_info = row.get("series") or {}
            episode = row.get("episode") or {}
            date = row.get("date")
            if not date:
                continue
            recent.append(
                {
                    "kind": "series",
                    "title": series_info.get("title"),
                    "season": episode.get("seasonNumber"),
                    "episode": episode.get("episodeNumber"),
                    "date": date,
                    "tmdb": series_info.get("tmdbId"),
                }
            )
        recent.sort(key=lambda item: str(item.get("date") or ""), reverse=True)

        radarr_records = radarr_queue.get("records") or []
        sonarr_records = sonarr_queue.get("records") or []
        warnings = sum(
            1
            for row in radarr_records + sonarr_records
            if (row.get("trackedDownloadStatus") or "").lower()
            in ("warning", "error")
            or row.get("errorMessage")
        )
        disk = _pick_disk(radarr_disks) or _pick_disk(sonarr_disks)

        return {
            "ok": not errors,
            "errors": errors,
            "fetched_at": time.time(),
            "radarr": {
                "version": radarr_status.get("version"),
                "movies_total": len(movies),
                "movies_available": len(movies_with_file),
                "size": sum(int(row.get("sizeOnDisk") or 0) for row in movies),
                "queue": int(radarr_queue.get("totalRecords") or 0),
                "queue_warnings": warnings,
            },
            "sonarr": {
                "version": sonarr_status.get("version"),
                "series_total": len(series),
                "series_available": series_available,
                "episodes_available": episodes_available,
                "episodes_total": episodes_total,
                "size": sum(int(row.get("sizeOnDisk") or 0) for row in series),
                "queue": int(sonarr_queue.get("totalRecords") or 0),
            },
            "disk": (
                {
                    "path": disk.get("path"),
                    "free": int(disk.get("freeSpace") or 0),
                    "total": int(disk.get("totalSpace") or 0),
                }
                if disk
                else None
            ),
            "recent": recent[:8],
            "movies_by_tmdb": movies_by_tmdb,
            "series_by_tmdb": series_by_tmdb,
        }

    # --------------------------------------------------------------- lookups

    def movie(self, tmdb: int) -> dict[str, Any] | None:
        data = self.overview()
        return (data.get("movies_by_tmdb") or {}).get(int(tmdb))

    def series(self, tmdb: int) -> dict[str, Any] | None:
        data = self.overview()
        return (data.get("series_by_tmdb") or {}).get(int(tmdb))

    def in_library(
        self,
        kind: str,
        tmdb: int | None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> dict[str, Any]:
        if not tmdb:
            return {"in_library": False, "has_file": False}
        if kind == "movie":
            row = self.movie(int(tmdb))
            if row is None:
                return {"in_library": False, "has_file": False}
            return {
                "in_library": True,
                "has_file": bool(row.get("hasFile")),
                "title": row.get("title"),
            }
        row = self.series(int(tmdb))
        if row is None:
            return {"in_library": False, "has_file": False}
        if season and episode:
            return {
                "in_library": True,
                "has_file": self.episode_has_file(row, int(season), int(episode)),
                "title": row.get("title"),
            }
        return {
            "in_library": True,
            "has_file": _stat(row, "episodeFileCount") > 0,
            "title": row.get("title"),
            "episodes_available": _stat(row, "episodeFileCount"),
            "episodes_total": _stat(row, "totalEpisodeCount"),
        }

    def episode_has_file(self, series: dict, season: int, episode: int) -> bool:
        try:
            series_id = int(series.get("id") or 0)
        except (TypeError, ValueError):
            return False
        if not series_id:
            return False
        now = time.monotonic()
        with self._lock:
            cached = self._episodes.get(series_id)
            if cached is not None and now - cached[0] < self.ttl:
                episodes = cached[1]
            else:
                episodes = []
        if not episodes:
            try:
                episodes = self._sonarr().episodes(series_id)
            except Exception:
                episodes = []
            with self._lock:
                self._episodes[series_id] = (now, episodes)
        for row in episodes:
            try:
                if (
                    int(row.get("seasonNumber") or 0) == int(season)
                    and int(row.get("episodeNumber") or 0) == int(episode)
                ):
                    return bool(row.get("hasFile"))
            except (TypeError, ValueError):
                continue
        return False


def overview(config: Any, *, force: bool = False) -> dict[str, Any]:
    """Atajo sin cache compartida; el panel usa una instancia de ArrData."""
    return ArrData(config).overview(force=force)
