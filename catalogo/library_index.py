"""Índice de lo que ya está descargado en Radarr/Sonarr, vía API.

No se leen sus bases de datos: se usan las API keys configuradas en TOD.
Sirve para no exportar ni descargar títulos que ya están en las bibliotecas
oficiales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LibraryIndex:
    movies: set[int] = field(default_factory=set)
    episodes: set[tuple[int, int, int]] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.movies and not self.episodes


def _tmdb(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_library_index(radarr=None, sonarr=None) -> LibraryIndex:
    index = LibraryIndex()

    if radarr is not None:
        try:
            for movie in radarr.movies():
                tmdb = _tmdb(movie.get("tmdbId"))
                if tmdb and movie.get("hasFile"):
                    index.movies.add(tmdb)
        except Exception as exc:
            index.errors.append(f"radarr: {str(exc)[:160]}")

    if sonarr is not None:
        try:
            for series in sonarr.series():
                tmdb = _tmdb(series.get("tmdbId"))
                series_id = series.get("id")
                if not tmdb or series_id is None:
                    continue
                for episode in sonarr.episodes(int(series_id)):
                    if not episode.get("hasFile"):
                        continue
                    season = int(episode.get("seasonNumber") or 0)
                    number = int(episode.get("episodeNumber") or 0)
                    if season >= 0 and number > 0:
                        index.episodes.add((tmdb, season, number))
        except Exception as exc:
            index.errors.append(f"sonarr: {str(exc)[:160]}")

    return index


def in_library(index: LibraryIndex, asset) -> bool:
    try:
        tmdb = int(asset["tmdb"] or 0)
    except (TypeError, ValueError, KeyError):
        return False
    if not tmdb:
        return False
    if asset["kind"] == "movie":
        return tmdb in index.movies
    try:
        season = int(asset["season"] or 0)
        episode = int(asset["episode"] or 0)
    except (TypeError, ValueError):
        return False
    return (tmdb, season, episode) in index.episodes
