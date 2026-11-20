"""Puente catálogo → Radarr/Sonarr.

Copia el fichero que ya descargó el catálogo a la carpeta que el *arr ya
tiene para ese título y dispara un reescaneo. El catálogo conserva su copia.
"""

from __future__ import annotations

import os
import shutil
from contextlib import closing
from pathlib import Path

from .db import connect
from .strm import safe_name


def _source(row) -> Path | None:
    for column in ("local_path", "library_path"):
        value = row[column]
        if value and Path(value).is_file():
            return Path(value)
    return None


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, temporary)
    os.replace(temporary, dst)


def bridge_downloads(
    db_path,
    radarr,
    sonarr,
    *,
    limit: int = 1,
    dry_run: bool = True,
) -> dict:
    report: dict = {
        "checked": 0,
        "bridged": [],
        "skipped": [],
        "errors": [],
        "dry_run": dry_run,
    }
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.id AS torrent_id, t.info_hash,
                   m.id AS asset_id, m.key, m.kind, m.tmdb, m.title, m.year,
                   m.season, m.episode, f.local_path, f.library_path
            FROM torrents t
            JOIN torrent_files f ON f.torrent_id = t.id
            JOIN media_items m ON m.id = f.asset_id
            WHERE t.category = 'ondemand'
              AND t.progress >= 1
              AND f.state IN ('ready', 'archived', 'seeding')
              AND f.asset_id IS NOT NULL
            ORDER BY t.id DESC, f.id DESC
            LIMIT ?
            """,
            (max(50, int(limit) * 20),),
        ).fetchall()
    bridged_count = 0
    for row in rows:
        if bridged_count >= int(limit):
            break
        report["checked"] += 1
        asset = dict(row)
        source = _source(row)
        if source is None:
            report["skipped"].append(
                {"key": asset["key"], "reason": "sin_fichero"}
            )
            continue
        try:
            tmdb = int(asset["tmdb"] or 0)
        except (TypeError, ValueError):
            tmdb = 0
        if not tmdb:
            report["skipped"].append(
                {"key": asset["key"], "reason": "sin_tmdb"}
            )
            continue
        if asset["kind"] == "movie":
            if radarr is None:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "sin_radarr"}
                )
                continue
            try:
                movie = radarr.movie_by_tmdb(tmdb)
            except Exception as exc:
                report["errors"].append(
                    f"{asset['key']}: {str(exc)[:150]}"
                )
                continue
            if movie is None:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "no_en_radarr"}
                )
                continue
            if movie.get("hasFile"):
                report["skipped"].append(
                    {"key": asset["key"], "reason": "ya_en_radarr"}
                )
                continue
            title = safe_name(movie.get("title") or asset["title"] or "Sin titulo")
            year = movie.get("year") or asset["year"]
            suffix = f" ({year})" if year else ""
            destination = Path(movie["path"]) / f"{title}{suffix}{source.suffix}"
            if destination.exists():
                report["skipped"].append(
                    {"key": asset["key"], "reason": "destino_existe"}
                )
                continue
            if dry_run:
                report["bridged"].append(
                    {"key": asset["key"], "to": str(destination)}
                )
                bridged_count += 1
                continue
            try:
                _copy(source, destination)
                radarr.rescan_movie(movie["id"])
            except Exception as exc:
                report["errors"].append(
                    f"{asset['key']}: {str(exc)[:150]}"
                )
                continue
            report["bridged"].append(
                {"key": asset["key"], "to": str(destination)}
            )
            bridged_count += 1
        else:
            if sonarr is None:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "sin_sonarr"}
                )
                continue
            try:
                series = sonarr.series_by_tmdb(tmdb)
                if series is None:
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "no_en_sonarr"}
                    )
                    continue
                episode = sonarr.episode(
                    series["id"],
                    int(asset["season"] or 0),
                    int(asset["episode"] or 0),
                )
            except Exception as exc:
                report["errors"].append(
                    f"{asset['key']}: {str(exc)[:150]}"
                )
                continue
            if episode is None:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "episodio_no_en_sonarr"}
                )
                continue
            if episode.get("hasFile"):
                report["skipped"].append(
                    {"key": asset["key"], "reason": "ya_en_sonarr"}
                )
                continue
            season = int(asset["season"] or 0)
            number = int(asset["episode"] or 0)
            series_title = safe_name(
                series.get("title") or asset["title"] or "Serie"
            )
            episode_title = safe_name(
                episode.get("title") or f"S{season:02d}E{number:02d}"
            )
            destination = (
                Path(series["path"])
                / f"Season {season}"
                / f"{series_title} - S{season:02d}E{number:02d} - "
                  f"{episode_title}{source.suffix}"
            )
            if destination.exists():
                report["skipped"].append(
                    {"key": asset["key"], "reason": "destino_existe"}
                )
                continue
            if dry_run:
                report["bridged"].append(
                    {"key": asset["key"], "to": str(destination)}
                )
                bridged_count += 1
                continue
            try:
                _copy(source, destination)
                sonarr.rescan_series(series["id"])
            except Exception as exc:
                report["errors"].append(
                    f"{asset['key']}: {str(exc)[:150]}"
                )
                continue
            report["bridged"].append(
                {"key": asset["key"], "to": str(destination)}
            )
            bridged_count += 1
    return report
