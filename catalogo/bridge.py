"""Puente catálogo → Radarr/Sonarr.

Cuando TOD descarga algo bajo demanda, lo registra en el *arr correspondiente
para que no vuelva a bajarlo:

- si el título no existe, lo añade sin lanzar búsqueda;
- coloca el fichero en la carpeta del *arr (hardlink si se puede);
- fuerza un reescaneo;
- cancela la entrada de cola duplicada.

El catálogo conserva su copia y sigue sembrando según su política.
"""

from __future__ import annotations

import os
import shutil
from contextlib import closing
from pathlib import Path
from typing import Any

from .db import connect
from .strm import safe_name


def _source(row) -> Path | None:
    for column in ("local_path", "library_path"):
        value = row[column]
        if value and Path(value).is_file():
            return Path(value)
    return None


def _place(src: Path, dst: Path) -> str:
    """Coloca el fichero sin duplicar disco cuando es posible."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        pass
    temporary = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, temporary)
    os.replace(temporary, dst)
    return "copy"


def _first_path(rows: list[dict]) -> str | None:
    for row in rows:
        path = str(row.get("path") or "").strip()
        if path and row.get("accessible", True):
            return path
    return None


def _first_profile(rows: list[dict]) -> int | None:
    try:
        return int(rows[0]["id"]) if rows else None
    except (KeyError, TypeError, ValueError):
        return None


def _cancel_queue(client, *, movie_id: int | None = None,
                  episode_id: int | None = None) -> int:
    if movie_id is None and episode_id is None:
        return 0
    try:
        data = client.queue()
        records = data.get("records") or [] if isinstance(data, dict) else []
    except Exception:
        return 0
    removed = 0
    for record in records:
        try:
            if movie_id is not None:
                match = int(record.get("movieId") or 0) == int(movie_id)
            else:
                match = int(record.get("episodeId") or 0) == int(
                    episode_id or 0
                )
            if not match:
                continue
            # Nunca se borra la descarga del cliente: el *arr puede tener
            # otro release en curso y, si comparte hash, se llevaría por
            # delante la de TOD. El rescan del *arr cierra su propia cola.
            client.delete_queue_item(
                int(record["id"]),
                remove_from_client=False,
                blocklist=False,
            )
            removed += 1
        except Exception:
            continue
    return removed


def bridge_downloads(
    db_path,
    radarr,
    sonarr,
    *,
    limit: int = 1,
    dry_run: bool = True,
) -> dict:
    """Copia a Radarr/Sonarr lo descargado por TOD.

    `limit` cuenta descargas completas: un pack de temporada se puentea
    entero en la misma pasada, con un solo reescaneo de la serie.
    """
    report: dict[str, Any] = {
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
                   m.id AS asset_id, m.key, m.kind, m.tmdb, m.tvdb_id,
                   m.title, m.year, m.season, m.episode,
                   f.local_path, f.library_path
            FROM torrents t
            JOIN torrent_files f ON f.torrent_id = t.id
            JOIN media_items m ON m.id = f.asset_id
            WHERE t.category = 'ondemand'
              AND f.state IN ('ready', 'archived', 'seeding')
              AND f.asset_id IS NOT NULL
            ORDER BY t.id DESC, f.id DESC
            LIMIT ?
            """,
            # Se recorren varias descargas completas: el límite se aplica al
            # número de torrents, no a filas.
            (max(500, int(limit) * 50),),
        ).fetchall()

    radarr_root: str | None = None
    radarr_profile: int | None = None
    sonarr_root: str | None = None
    sonarr_profile: int | None = None

    def movie_defaults() -> tuple[str | None, int | None]:
        nonlocal radarr_root, radarr_profile
        if radarr_root is None:
            radarr_root = _first_path(radarr.root_folders())
        if radarr_profile is None:
            radarr_profile = _first_profile(radarr.quality_profiles())
        return radarr_root, radarr_profile

    def series_defaults() -> tuple[str | None, int | None]:
        nonlocal sonarr_root, sonarr_profile
        if sonarr_root is None:
            sonarr_root = _first_path(sonarr.root_folders())
        if sonarr_profile is None:
            sonarr_profile = _first_profile(sonarr.quality_profiles())
        return sonarr_root, sonarr_profile

    bridged_count = 0
    # `limit` cuenta descargas, no ficheros: un pack se puentea entero en la
    # misma pasada, con un solo reescaneo de la serie.
    torrent_bridged = False
    current_torrent: int | None = None
    rescanned: set[int] = set()

    def count_bridge() -> None:
        nonlocal bridged_count, torrent_bridged
        if torrent_bridged:
            return
        bridged_count += 1
        torrent_bridged = True

    for row in rows:
        torrent_id = int(row["torrent_id"])
        if torrent_id != current_torrent:
            if bridged_count >= int(limit):
                break
            current_torrent = torrent_id
            torrent_bridged = False
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
                added = False
                if movie is None:
                    root, profile = movie_defaults()
                    if not root or not profile:
                        report["skipped"].append(
                            {"key": asset["key"], "reason": "sin_root_o_perfil"}
                        )
                        continue
                    if dry_run:
                        report["bridged"].append(
                            {
                                "key": asset["key"],
                                "action": "add",
                                "title": asset["title"],
                            }
                        )
                        count_bridge()
                        continue
                    created = radarr.add_movie(
                        tmdb=tmdb,
                        title=asset["title"] or "",
                        year=asset["year"],
                        root_folder=root,
                        quality_profile_id=profile,
                        monitored=False,
                    )
                    movie = radarr.movie_by_tmdb(tmdb) or created
                    added = True
                if not movie:
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "no_en_radarr"}
                    )
                    continue
                if movie.get("hasFile"):
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "ya_en_radarr"}
                    )
                    continue
                movie_path = str(movie.get("path") or "").strip()
                if not movie_path:
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "radarr_sin_ruta"}
                    )
                    continue
                title = safe_name(
                    movie.get("title") or asset["title"] or "Sin titulo"
                )
                year = movie.get("year") or asset["year"]
                suffix = f" ({year})" if year else ""
                destination = Path(movie_path) / f"{title}{suffix}{source.suffix}"
                if destination.exists():
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "destino_existe"}
                    )
                    continue
                if dry_run:
                    report["bridged"].append(
                        {"key": asset["key"], "to": str(destination),
                         "action": "add" if added else "copy"}
                    )
                    count_bridge()
                    continue
                action = _place(source, destination)
                radarr.rescan_movie(int(movie["id"]))
                removed = _cancel_queue(radarr, movie_id=int(movie["id"]))
                report["bridged"].append(
                    {
                        "key": asset["key"],
                        "to": str(destination),
                        "action": "added" if added else action,
                        "queue_removed": removed,
                    }
                )
                count_bridge()
            except Exception as exc:
                report["errors"].append(
                    f"{asset['key']}: {str(exc)[:150]}"
                )
            continue

        if sonarr is None:
            report["skipped"].append(
                {"key": asset["key"], "reason": "sin_sonarr"}
            )
            continue
        try:
            series = sonarr.series_by_tmdb(tmdb)
            added = False
            if series is None:
                root, profile = series_defaults()
                if not root or not profile:
                    report["skipped"].append(
                        {"key": asset["key"], "reason": "sin_root_o_perfil"}
                    )
                    continue
                if dry_run:
                    report["bridged"].append(
                        {
                            "key": asset["key"],
                            "action": "add",
                            "title": asset["title"],
                        }
                    )
                    count_bridge()
                    continue
                created = sonarr.add_series(
                    tvdb=asset.get("tvdb_id"),
                    tmdb=tmdb,
                    title=asset["title"] or "",
                    year=asset["year"],
                    root_folder=root,
                    quality_profile_id=profile,
                    monitored=False,
                )
                series = sonarr.series_by_tmdb(tmdb) or created
                added = True
            if not series:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "no_en_sonarr"}
                )
                continue
            episode = sonarr.episode(
                series["id"],
                int(asset["season"] or 0),
                int(asset["episode"] or 0),
            )
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
            series_path = str(series.get("path") or "").strip()
            if not series_path:
                report["skipped"].append(
                    {"key": asset["key"], "reason": "sonarr_sin_ruta"}
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
                Path(series_path)
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
                    {"key": asset["key"], "to": str(destination),
                     "action": "add" if added else "copy"}
                )
                count_bridge()
                continue
            action = _place(source, destination)
            # Se reescanea al final, cuando el pack ya está copiado entero.
            rescanned.add(int(series["id"]))
            removed = _cancel_queue(
                sonarr, episode_id=episode.get("id")
            )
            report["bridged"].append(
                {
                    "key": asset["key"],
                    "to": str(destination),
                    "action": "added" if added else action,
                    "queue_removed": removed,
                }
            )
            count_bridge()
        except Exception as exc:
            report["errors"].append(
                f"{asset['key']}: {str(exc)[:150]}"
            )
    for series_id in sorted(rescanned):
        try:
            sonarr.rescan_series(int(series_id))
        except Exception as exc:
            report["errors"].append(
                f"series {series_id}: rescan: {str(exc)[:120]}"
            )
    return report
