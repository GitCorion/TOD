"""Reconstruye `library_files` desde las API de Radarr y Sonarr."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .db import connect


def _file_path(base: str | None, relative: str | None) -> str | None:
    if not relative:
        return None
    path = Path(relative)
    if path.is_absolute():
        return str(path)
    if not base:
        return None
    return str(Path(base) / path)


def sync_library(db_path, *, radarr=None, sonarr=None) -> dict:
    entries: list[tuple] = []
    movies = 0
    episodes = 0
    errors: list[str] = []

    if radarr is not None:
        try:
            for movie in radarr.movies():
                if not movie.get("hasFile"):
                    continue
                tmdb = int(movie.get("tmdbId") or 0)
                movie_file = movie.get("movieFile") or {}
                path = movie_file.get("path") or _file_path(
                    movie.get("path"), movie_file.get("relativePath")
                )
                if not tmdb or not path:
                    continue
                entries.append(
                    (
                        "movie",
                        tmdb,
                        0,
                        0,
                        str(path),
                        int(movie_file.get("size") or 0),
                        "radarr",
                    )
                )
                movies += 1
        except Exception as exc:
            errors.append(f"radarr: {str(exc)[:160]}")

    if sonarr is not None:
        try:
            for series in sonarr.series():
                tmdb = int(series.get("tmdbId") or 0)
                series_id = series.get("id")
                if not tmdb or series_id is None:
                    continue
                files = {
                    int(item.get("id") or 0): item
                    for item in sonarr.episode_files(int(series_id))
                }
                for episode in sonarr.episodes(int(series_id)):
                    if not episode.get("hasFile"):
                        continue
                    file_id = int(episode.get("episodeFileId") or 0)
                    item = files.get(file_id)
                    if item is None:
                        continue
                    path = item.get("path") or _file_path(
                        series.get("path"), item.get("relativePath")
                    )
                    if not path:
                        continue
                    season = int(episode.get("seasonNumber") or 0)
                    number = int(episode.get("episodeNumber") or 0)
                    if number <= 0:
                        continue
                    entries.append(
                        (
                            "series",
                            tmdb,
                            season,
                            number,
                            str(path),
                            int(item.get("size") or 0),
                            "sonarr",
                        )
                    )
                    episodes += 1
        except Exception as exc:
            errors.append(f"sonarr: {str(exc)[:160]}")

    if radarr is None and sonarr is None:
        return {
            "ok": False,
            "error": "sin Radarr/Sonarr configurados (API key)",
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
        "errors": errors,
    }
