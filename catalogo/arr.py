"""Clientes mínimos de Radarr y Sonarr para el puente del catálogo."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class ArrError(RuntimeError):
    pass


class _ArrClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 30.0):
        if not api_key:
            raise ArrError("sin API key de Radarr/Sonarr")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, path: str, *, method: str = "GET",
                 params: dict | None = None, payload: dict | None = None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
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
            raise ArrError(f"HTTP {exc.code} en {path}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ArrError(f"no responde: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArrError("respuesta JSON inválida") from exc

    def command(self, payload: dict) -> None:
        self._request("/command", method="POST", payload=payload)

    def status(self) -> dict:
        data = self._request("/system/status")
        return data if isinstance(data, dict) else {}

    def diskspace(self) -> list[dict]:
        data = self._request("/diskspace")
        return data if isinstance(data, list) else []

    def history(self, *, page_size: int = 20) -> list[dict]:
        data = self._request(
            "/history",
            params={
                "page": 1,
                "pageSize": max(1, int(page_size)),
                "sortKey": "date",
                "sortDirection": "descending",
            },
        )
        rows = (data or {}).get("records") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    def _queue(self, unknown_param: str, *, page_size: int = 1000) -> dict:
        records: list[dict] = []
        page = 1
        total = 0
        while True:
            data = self._request(
                "/queue",
                params={
                    "page": page,
                    "pageSize": max(1, int(page_size)),
                    unknown_param: "true",
                },
            ) or {}
            rows = data.get("records") or []
            total = int(data.get("totalRecords") or 0)
            records.extend(rows)
            if not rows or len(records) >= total:
                break
            page += 1
        return {"records": records, "totalRecords": total}

    def queue(self, *, page_size: int = 1000) -> dict:
        raise NotImplementedError


class RadarrClient(_ArrClient):
    @classmethod
    def from_config(cls, config) -> "RadarrClient":
        return cls(config.radarr_url, config.radarr_api_key)

    def movies(self) -> list[dict]:
        data = self._request("/movie")
        return data if isinstance(data, list) else []

    def queue(self, *, page_size: int = 1000) -> dict:
        return self._queue(
            "includeUnknownMovieItems", page_size=page_size
        )

    def movie_by_tmdb(self, tmdb: int) -> dict | None:
        data = self._request("/movie", params={"tmdbId": int(tmdb)})
        rows = data if isinstance(data, list) else [data] if data else []
        for row in rows:
            try:
                if int(row.get("tmdbId") or 0) == int(tmdb):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def rescan_movie(self, movie_id: int) -> None:
        self.command({"name": "RescanMovie", "movieId": int(movie_id)})


class SonarrClient(_ArrClient):
    @classmethod
    def from_config(cls, config) -> "SonarrClient":
        return cls(config.sonarr_url, config.sonarr_api_key)

    def series(self) -> list[dict]:
        data = self._request("/series")
        return data if isinstance(data, list) else []

    def queue(self, *, page_size: int = 1000) -> dict:
        return self._queue(
            "includeUnknownSeriesItems", page_size=page_size
        )

    def series_by_tmdb(self, tmdb: int) -> dict | None:
        rows = self._request("/series")
        for row in rows or []:
            try:
                if int(row.get("tmdbId") or 0) == int(tmdb):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def episodes(self, series_id: int) -> list[dict]:
        rows = self._request("/episode", params={"seriesId": int(series_id)})
        return rows if isinstance(rows, list) else []

    def episode(
        self, series_id: int, season: int, episode: int
    ) -> dict | None:
        for row in self.episodes(series_id):
            try:
                if (
                    int(row.get("seasonNumber") or 0) == int(season)
                    and int(row.get("episodeNumber") or 0) == int(episode)
                ):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def rescan_series(self, series_id: int) -> None:
        self.command({"name": "RescanSeries", "seriesId": int(series_id)})
