"""Índice de lo que ya está descargado en Radarr/Sonarr.

Se lee en solo lectura. Sirve para que el catálogo no exporte ni descargue
títulos que ya están en las bibliotecas oficiales.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LibraryIndex:
    movies: set[int] = field(default_factory=set)
    episodes: set[tuple[int, int, int]] = field(default_factory=set)

    def empty(self) -> bool:
        return not self.movies and not self.episodes


def _connect(path: str | Path | None):
    if not path:
        return None
    db = Path(path)
    if not db.is_file():
        return None
    uri = f"file:{db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def build_library_index(
    radarr_db: str | Path | None = None,
    sonarr_db: str | Path | None = None,
) -> LibraryIndex:
    index = LibraryIndex()
    conn = _connect(radarr_db)
    if conn is not None:
        try:
            with closing(conn):
                for row in conn.execute(
                    """
                    SELECT DISTINCT m.TmdbId
                    FROM Movies mv
                    JOIN MovieMetadata m ON m.Id = mv.MovieMetadataId
                    WHERE mv.MovieFileId > 0
                    """
                ):
                    try:
                        tmdb = int(row["TmdbId"] or 0)
                    except (TypeError, ValueError):
                        continue
                    if tmdb:
                        index.movies.add(tmdb)
        except sqlite3.Error:
            pass
    conn = _connect(sonarr_db)
    if conn is not None:
        try:
            with closing(conn):
                for row in conn.execute(
                    """
                    SELECT DISTINCT s.TmdbId, e.SeasonNumber, e.EpisodeNumber
                    FROM Series s
                    JOIN Episodes e ON e.SeriesId = s.Id
                    WHERE e.EpisodeFileId > 0 AND s.TmdbId IS NOT NULL
                    """
                ):
                    try:
                        tmdb = int(row["TmdbId"] or 0)
                        season = int(row["SeasonNumber"] or 0)
                        episode = int(row["EpisodeNumber"] or 0)
                    except (TypeError, ValueError):
                        continue
                    if tmdb and season >= 0 and episode > 0:
                        index.episodes.add((tmdb, season, episode))
        except sqlite3.Error:
            pass
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
