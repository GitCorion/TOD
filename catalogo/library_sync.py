"""Reconstruye `library_files` desde Radarr y Sonarr en solo lectura."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .db import connect


def _readonly(path) -> sqlite3.Connection | None:
    if not path:
        return None
    db = Path(path)
    if not db.is_file():
        return None
    conn = sqlite3.connect(
        f"file:{db}?mode=ro", uri=True, timeout=10
    )
    conn.row_factory = sqlite3.Row
    return conn


def sync_library(
    db_path,
    *,
    radarr_db=None,
    sonarr_db=None,
) -> dict:
    entries: list[tuple] = []
    movies = 0
    episodes = 0
    readable = 0

    conn = _readonly(radarr_db)
    if conn is not None:
        try:
            with closing(conn):
                for row in conn.execute(
                    """
                    SELECT m.TmdbId, mv.Path, f.RelativePath, f.Size
                    FROM Movies mv
                    JOIN MovieMetadata m ON m.Id = mv.MovieMetadataId
                    JOIN MovieFiles f ON f.MovieId = mv.Id
                    WHERE mv.MovieFileId > 0 AND m.TmdbId IS NOT NULL
                    """
                ):
                    try:
                        tmdb = int(row["TmdbId"] or 0)
                    except (TypeError, ValueError):
                        continue
                    if not tmdb or not row["Path"] or not row["RelativePath"]:
                        continue
                    path = str(Path(row["Path"]) / row["RelativePath"])
                    entries.append(
                        ("movie", tmdb, 0, 0, path,
                         int(row["Size"] or 0), "radarr")
                    )
                    movies += 1
            readable += 1
        except sqlite3.Error:
            pass

    conn = _readonly(sonarr_db)
    if conn is not None:
        try:
            with closing(conn):
                for row in conn.execute(
                    """
                    SELECT s.TmdbId, s.Path, e.SeasonNumber, e.EpisodeNumber,
                           f.RelativePath, f.Size
                    FROM Episodes e
                    JOIN EpisodeFiles f ON f.Id = e.EpisodeFileId
                    JOIN Series s ON s.Id = e.SeriesId
                    WHERE e.EpisodeFileId > 0 AND s.TmdbId IS NOT NULL
                    """
                ):
                    try:
                        tmdb = int(row["TmdbId"] or 0)
                        season = int(row["SeasonNumber"] or 0)
                        episode = int(row["EpisodeNumber"] or 0)
                    except (TypeError, ValueError):
                        continue
                    if (
                        not tmdb
                        or not row["Path"]
                        or not row["RelativePath"]
                        or episode <= 0
                    ):
                        continue
                    path = str(Path(row["Path"]) / row["RelativePath"])
                    entries.append(
                        ("series", tmdb, season, episode, path,
                         int(row["Size"] or 0), "sonarr")
                    )
                    episodes += 1
            readable += 1
        except sqlite3.Error:
            pass

    if readable == 0:
        return {
            "ok": False,
            "error": "sin bases de Radarr/Sonarr legibles",
            "entries": 0,
        }
    with closing(connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM library_files")
        db.executemany(
            """
            INSERT OR REPLACE INTO library_files(
                kind, tmdb, season, episode, path, size_bytes, source
            ) VALUES(?,?,?,?,?,?,?)
            """,
            entries,
        )
        db.commit()
    return {
        "ok": True,
        "entries": len(entries),
        "movies": movies,
        "episodes": episodes,
    }
