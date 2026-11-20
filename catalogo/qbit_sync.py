"""Sync de solo lectura de qBittorrent + dedup Radarr/Sonarr.

Reglas:
  - Nunca llama a endpoints mutantes de qBittorrent.
  - `torrents/info` se lee una sola vez.
  - `torrents/files` solo para categorías propias, completos o ya mapeados.
  - Los Radarr en cola al 0% no se tocan.
  - El dedup solo reutiliza torrents con progress == 1 y fichero existente.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .policy import get_policy
from .qbittorrent import QbittorrentClient

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".webm", ".mov"}
SXXEYY = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
XXYY = re.compile(r"(?i)\b(\d{1,2})x(\d{2,3})\b")
ONDEMAND_CATEGORIES = {"ondemand", "catalogo"}


@dataclass
class SyncReport:
    torrents_seen: int = 0
    torrents_written: int = 0
    files_fetched: int = 0
    files_written: int = 0
    media_created: int = 0
    media_linked: int = 0
    torrents_removed: int = 0
    reseed_required: int = 0
    missing_files: int = 0
    errors: list[str] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "torrents_seen": self.torrents_seen,
            "torrents_written": self.torrents_written,
            "files_fetched": self.files_fetched,
            "files_written": self.files_written,
            "media_created": self.media_created,
            "media_linked": self.media_linked,
            "torrents_removed": self.torrents_removed,
            "reseed_required": self.reseed_required,
            "missing_files": self.missing_files,
            "by_category": dict(sorted(self.by_category.items())),
            "errors": self.errors[:20],
            "dry_run": self.dry_run,
        }


def _iso_from_epoch(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()


def _storage_for(path: str | None, archive_root: str | None = None) -> str:
    if not path:
        return "NONE"
    archive = str(archive_root or "/mnt/library")
    if path.startswith(archive):
        return "1FICHIER"
    if path.startswith(("/mnt/multimedia", "/mnt/descargas", "/mnt/cache")):
        return "LOCAL_DISK"
    return "NONE"


def _qbit_state_to_db(state: str | None, progress: float) -> str:
    text = (state or "").lower()
    if "error" in text or "missing" in text:
        return "error"
    if text.startswith("paused") or text.startswith("stopped"):
        return "paused"
    if progress >= 1:
        return "seeding" if text.endswith("up") or "upload" in text else "complete"
    if "up" in text and progress >= 1:
        return "seeding"
    if "dl" in text or "download" in text or "meta" in text:
        return "downloading"
    if "check" in text or "queued" in text:
        return "waiting"
    return "downloading" if progress < 1 else "seeding"


def _seed_started_at(torrent: dict, now: datetime) -> str | None:
    try:
        seconds = int(torrent.get("seeding_time") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return None
    return (now - timedelta(seconds=seconds)).isoformat()


def _base_path(torrent: dict, progress: float) -> str | None:
    if progress >= 1:
        return torrent.get("save_path") or torrent.get("content_path")
    return (
        torrent.get("download_path")
        or torrent.get("save_path")
        or os.path.dirname(torrent.get("content_path") or "")
        or None
    )


def _episode_from_name(name: str) -> tuple[int, int] | None:
    match = SXXEYY.search(name) or XXYY.search(name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _video(files: list[dict]) -> list[dict]:
    return [
        item for item in files
        if Path(item.get("name") or "").suffix.lower() in VIDEO_EXT
    ]


def _main_video(files: list[dict]) -> dict | None:
    videos = _video(files)
    if not videos:
        return None
    return max(videos, key=lambda item: int(item.get("size") or 0))


def radarr_index(client) -> dict[str, dict]:
    """DownloadId -> película (tmdb/título/año), vía API de Radarr."""
    result: dict[str, dict] = {}
    if client is None:
        return result
    try:
        movies = {
            int(item.get("id") or 0): item for item in client.movies()
        }
        for row in client.history_all():
            download_id = str(row.get("downloadId") or "").lower()
            if not download_id:
                continue
            movie = movies.get(int(row.get("movieId") or 0))
            if not movie:
                continue
            result[download_id] = {
                "tmdb": int(movie.get("tmdbId") or 0),
                "title": movie.get("title"),
                "year": movie.get("year"),
            }
    except Exception:
        return result
    return result


def sonarr_index(client) -> dict[str, list[dict]]:
    """DownloadId -> episodios (tmdb/tvdb/temporada/episodio), vía API."""
    result: dict[str, list[dict]] = defaultdict(list)
    if client is None:
        return dict(result)
    try:
        series_map = {
            int(item.get("id") or 0): item for item in client.series()
        }
        episode_cache: dict[int, dict[int, dict]] = {}
        for row in client.history_all():
            download_id = str(row.get("downloadId") or "").lower()
            if not download_id:
                continue
            series_id = int(row.get("seriesId") or 0)
            series = series_map.get(series_id)
            if not series:
                continue
            if series_id not in episode_cache:
                episode_cache[series_id] = {
                    int(item.get("id") or 0): item
                    for item in client.episodes(series_id)
                }
            episode = episode_cache[series_id].get(
                int(row.get("episodeId") or 0)
            )
            if not episode:
                continue
            item = {
                "tmdb": int(series.get("tmdbId") or 0),
                "tvdb": int(series.get("tvdbId") or 0),
                "season": int(episode.get("seasonNumber") or 0),
                "episode": int(episode.get("episodeNumber") or 0),
                "title": series.get("title"),
            }
            if item not in result[download_id]:
                result[download_id].append(item)
    except Exception:
        return dict(result)
    return dict(result)


def _ensure_movie(conn, tmdb: int, title: str | None, year: int | None) -> tuple[int | None, bool]:
    if not tmdb:
        return None, False
    key = f"movie:tmdb:{tmdb}"
    row = conn.execute(
        "SELECT id FROM media_items WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return int(row["id"]), False
    conn.execute(
        """
        INSERT INTO media_items(key, media_id, kind, tmdb, title, year,
                                identity_status, status)
        VALUES (?, ?, 'movie', ?, ?, ?, 'ok', 'COLD')
        ON CONFLICT(key) DO UPDATE SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (key, key, tmdb, title, year),
    )
    return int(
        conn.execute("SELECT id FROM media_items WHERE key = ?", (key,)).fetchone()["id"]
    ), True


def _ensure_episode(
    conn,
    *,
    tmdb: int,
    tvdb: int,
    season: int,
    episode: int,
    title: str | None,
) -> tuple[int | None, bool]:
    if tmdb:
        key = f"series:tmdb:{tmdb}:{season}:{episode}"
        scheme = "ok"
    elif tvdb:
        key = f"series:tvdb:{tvdb}:{season}:{episode}"
        scheme = "needs_identity"
    else:
        return None, False
    row = conn.execute(
        "SELECT id FROM media_items WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return int(row["id"]), False
    conn.execute(
        """
        INSERT INTO media_items(key, media_id, kind, tmdb, tvdb_id, season,
                                episode, title, identity_status, status)
        VALUES (?, ?, 'series', ?, ?, ?, ?, ?, ?, 'COLD')
        ON CONFLICT(key) DO UPDATE SET
            tvdb_id = COALESCE(media_items.tvdb_id, excluded.tvdb_id),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (
            key,
            key,
            tmdb or None,
            tvdb or None,
            season,
            episode,
            title,
            scheme,
        ),
    )
    return int(
        conn.execute("SELECT id FROM media_items WHERE key = ?", (key,)).fetchone()["id"]
    ), True


def _upsert_torrent(conn, torrent: dict, now: datetime) -> int:
    info_hash = (torrent.get("hash") or "").lower()
    progress = float(torrent.get("progress") or 0)
    category = torrent.get("category") or ""
    state = _qbit_state_to_db(torrent.get("state"), progress)
    existing = conn.execute(
        "SELECT id, release_id FROM torrents WHERE info_hash = ?", (info_hash,)
    ).fetchone()
    values = (
        torrent.get("tracker"),
        state,
        torrent.get("save_path"),
        torrent.get("download_path"),
        _iso_from_epoch(torrent.get("added_on")),
        _iso_from_epoch(torrent.get("completion_on")),
        _seed_started_at(torrent, now),
        int(torrent.get("uploaded") or 0),
        int(torrent.get("downloaded") or 0),
        float(torrent.get("ratio") or 0),
        now.isoformat(),
        category,
        torrent.get("state"),
        progress,
        _base_path(torrent, progress),
        torrent.get("content_path"),
        info_hash,
    )
    if existing:
        conn.execute(
            """
            UPDATE torrents SET
                tracker = ?, state = ?, save_path = ?, temp_path = ?,
                added_at = ?, completed_at = ?, seed_started_at = ?,
                uploaded = ?, downloaded = ?, ratio = ?, last_sync = ?,
                category = ?, qbit_state = ?, progress = ?, base_path = ?,
                content_path = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE info_hash = ?
            """,
            values[:-1] + (info_hash,),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO torrents(info_hash, tracker, state, save_path, temp_path,
                             added_at, completed_at, seed_started_at, uploaded,
                             downloaded, ratio, last_sync, category, qbit_state,
                             progress, base_path, content_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (info_hash,) + values[:-1],
    )
    return int(cur.lastrowid)


def _path_candidates(
    rel_path: str,
    *,
    save_path: str | None,
    base_path: str | None,
    content_path: str | None,
    category: str,
    archive_root: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    if content_path:
        candidates.append(content_path)
    for base in (save_path, base_path):
        if base:
            candidates.append(str(Path(base) / rel_path))
    archive = str(archive_root or "/mnt/library")
    roots = [
        archive,
        str(Path(archive) / "descargas"),
        "/mnt/descargas/ondemand",
        "/mnt/descargas/radarr",
        "/mnt/descargas/tv-sonarr",
        "/mnt/descargas/catalogo",
        "/mnt/descargas/ondemand",
        "/mnt/cache/tmp/radarr",
        "/mnt/cache/tmp/completo",
        "/mnt/cache/tmp/ondemand",
    ]
    for root in roots:
        candidates.append(str(Path(root) / rel_path))
    return list(dict.fromkeys(candidates))


def _upsert_file(
    conn,
    *,
    torrent_id: int,
    asset_id: int | None,
    item: dict,
    base_path: str | None,
    save_path: str | None,
    complete: bool,
    cloud_uploaded_at: str | None,
    report: SyncReport,
    check_exists: bool,
    moving: bool = False,
    content_path: str | None = None,
    category: str = "",
    archive_root: str | None = None,
) -> int:
    file_index = int(item.get("index") or 0)
    rel_path = item.get("name") or ""
    size = int(item.get("size") or 0)
    progress = float(item.get("progress") or 0)
    downloaded = int(size * progress)

    if moving:
        candidates = _path_candidates(
            rel_path,
            save_path=save_path,
            base_path=base_path,
            content_path=content_path,
            category=category,
            archive_root=archive_root,
        )
        path = next(
            (candidate for candidate in candidates if Path(candidate).is_file()),
            None,
        )
        if path is None and not check_exists:
            path = candidates[0] if candidates else None
        # Mientras se mueve, la ruta de destino puede no existir todavía.
        if path is None and save_path:
            path = str(Path(save_path) / rel_path)
        storage = _storage_for(path, archive_root)
        exists = bool(path) if not check_exists else bool(path and Path(path).is_file())
        if storage == "1FICHIER" and exists:
            local_path = None
            library_path = path
        else:
            local_path = path if exists else None
            library_path = None
        state = "archived" if storage == "1FICHIER" and exists else (
            "ready" if exists else "downloading"
        )
        # El TTL de archivo empieza cuando el move termina, no ahora.
        cloud_uploaded_at = None
    else:
        base = save_path if complete else base_path
        path = str(Path(base) / rel_path) if base else None
        storage = _storage_for(base, archive_root)
        exists = bool(path) if not check_exists else bool(path and Path(path).is_file())
        if complete and not exists and check_exists:
            report.missing_files += 1
        if storage == "1FICHIER":
            local_path = None
            library_path = path if exists or not check_exists else None
        else:
            local_path = path if exists or not check_exists else None
            library_path = None
        if complete and exists:
            state = "archived" if storage == "1FICHIER" else "ready"
        elif complete and not exists and check_exists:
            state = "missing"
        elif progress > 0:
            state = "downloading"
        else:
            state = "unknown"
    row = conn.execute(
        "SELECT id FROM torrent_files WHERE torrent_id = ? AND file_index = ?",
        (torrent_id, file_index),
    ).fetchone()
    values = (
        asset_id,
        rel_path,
        local_path,
        library_path,
        size,
        state,
        storage,
        downloaded,
        int(item.get("priority") or 0),
        cloud_uploaded_at,
        torrent_id,
        file_index,
    )
    if row:
        conn.execute(
            """
            UPDATE torrent_files SET
                asset_id = COALESCE(?, asset_id), rel_path = ?, local_path = ?,
                library_path = ?, size_bytes = ?, state = ?,
                storage_location = ?, downloaded_bytes = ?, priority = ?,
                cloud_uploaded_at = COALESCE(cloud_uploaded_at, ?),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE torrent_id = ? AND file_index = ?
            """,
            (asset_id, rel_path, local_path, library_path, size, state,
             storage, downloaded, int(item.get("priority") or 0),
             cloud_uploaded_at, torrent_id, file_index),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO torrent_files(asset_id, rel_path, local_path, library_path,
                                  size_bytes, state, storage_location,
                                  downloaded_bytes, priority, cloud_uploaded_at,
                                  torrent_id, file_index)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return int(cur.lastrowid)


def _choose_active(
    conn, asset_id: int, archive_root: str | None = None
) -> tuple[str, int | None]:
    rows = conn.execute(
        """
        SELECT f.*, r.health AS release_health, r.audio AS release_audio
        FROM torrent_files f
        LEFT JOIN releases r ON r.id = f.release_id
        WHERE f.asset_id = ? AND f.state <> 'missing'
        ORDER BY
            CASE
                WHEN r.health = 'blocked' THEN 2
                WHEN r.health = 'dead' THEN 1
                ELSE 0
            END,
            CASE
                WHEN r.audio = 'castellano' THEN 0
                WHEN r.audio IN ('spanish_unverified', 'espanol') THEN 1
                ELSE 2
            END,
            COALESCE(f.size_bytes, 0) DESC
        """,
        (asset_id,),
    ).fetchall()
    videos = [
        row for row in rows
        if Path(row["rel_path"] or "").suffix.lower() in VIDEO_EXT
    ] or rows
    if not videos:
        return "COLD", None
    active = videos[0]
    if active["torrent_id"] is not None:
        torrent = conn.execute(
            "SELECT qbit_state, save_path FROM torrents WHERE id = ?",
            (active["torrent_id"],),
        ).fetchone()
        if torrent and (torrent["qbit_state"] or "").lower() == "moving":
            archive = str(archive_root or "/mnt/library")
            if (torrent["save_path"] or "").startswith(archive):
                return "MOVING_TO_1FICHIER", int(active["id"])
            return "SEEDING_LOCAL", int(active["id"])
    if active["storage_location"] == "1FICHIER" and active["library_path"]:
        return "ON_1FICHIER", int(active["id"])
    if active["local_path"]:
        state = "SEEDING_LOCAL" if active["state"] in ("ready", "seeding") else "DOWNLOADING_LOCAL"
        return state, int(active["id"])
    return "COLD", None


def sync(
    db_path: str | os.PathLike[str],
    client: QbittorrentClient,
    *,
    radarr=None,
    sonarr=None,
    archive_root: str | None = None,
    dry_run: bool = False,
    check_exists: bool = True,
    now: datetime | None = None,
) -> SyncReport:
    current = now or datetime.now(timezone.utc)
    report = SyncReport(dry_run=dry_run)
    radarr = radarr_index(radarr)
    sonarr = sonarr_index(sonarr)

    torrents = client.torrents()
    report.torrents_seen = len(torrents)
    report.by_category = dict(Counter(t.get("category") or "" for t in torrents))

    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for torrent in torrents:
                info_hash = (torrent.get("hash") or "").lower()
                if not info_hash:
                    continue
                try:
                    torrent_id = _upsert_torrent(conn, torrent, current)
                    report.torrents_written += 1
                    category = torrent.get("category") or ""
                    progress = float(torrent.get("progress") or 0)
                    direct = conn.execute(
                        """
                        SELECT r.asset_id, m.kind, m.season, m.episode
                        FROM torrents t
                        JOIN releases r ON r.id = t.release_id
                        JOIN media_items m ON m.id = r.asset_id
                        WHERE t.id = ?
                        """,
                        (torrent_id,),
                    ).fetchone()
                    existing_files = conn.execute(
                        "SELECT COUNT(*) FROM torrent_files WHERE torrent_id = ?",
                        (torrent_id,),
                    ).fetchone()[0]
                    need_files = (
                        category in ONDEMAND_CATEGORIES
                        or progress >= 1
                        or bool(existing_files)
                    )
                    if not need_files:
                        continue
                    files = client.files(info_hash)
                    report.files_fetched += len(files)
                    complete = progress >= 1
                    moving = complete and (torrent.get("state") or "").lower() == "moving"
                    cloud = (
                        None
                        if moving
                        else (
                            _iso_from_epoch(torrent.get("completion_on"))
                            or current.isoformat()
                            if complete
                            and _storage_for(
                                torrent.get("save_path"), archive_root
                            )
                            == "1FICHIER"
                            else None
                        )
                    )
                    base_path = _base_path(torrent, progress)
                    save_path = torrent.get("save_path")

                    # Mapeo de media
                    movie = radarr.get(info_hash) if category != "tv-sonarr" else None
                    episodes = sonarr.get(info_hash, []) if category != "radarr" else []
                    movie_id = None
                    if movie and complete and movie.get("tmdb"):
                        movie_id, created = _ensure_movie(
                            conn, movie["tmdb"], movie.get("title"), movie.get("year")
                        )
                        if movie_id:
                            report.media_linked += 1
                            if created:
                                report.media_created += 1
                    video = _main_video(files)
                    episode_map: dict[tuple[int, int], int] = {}
                    for episode in episodes:
                        media_id, created = _ensure_episode(
                            conn,
                            tmdb=episode["tmdb"],
                            tvdb=episode["tvdb"],
                            season=episode["season"],
                            episode=episode["episode"],
                            title=episode.get("title"),
                        )
                        if media_id:
                            episode_map[(episode["season"], episode["episode"])] = media_id
                            report.media_linked += 1
                            if created:
                                report.media_created += 1

                    for item in files:
                        asset_id = None
                        if movie_id and video and item is video:
                            asset_id = movie_id
                        elif episode_map:
                            parsed = _episode_from_name(item.get("name") or "")
                            if parsed in episode_map:
                                asset_id = episode_map[parsed]
                        if asset_id is None and direct:
                            if direct["kind"] == "movie" and video and item is video:
                                asset_id = direct["asset_id"]
                            elif direct["kind"] == "series":
                                parsed = _episode_from_name(item.get("name") or "")
                                if parsed == (direct["season"], direct["episode"]):
                                    asset_id = direct["asset_id"]
                        if asset_id is None and existing_files:
                            row = conn.execute(
                                "SELECT asset_id FROM torrent_files "
                                "WHERE torrent_id = ? AND file_index = ?",
                                (torrent_id, int(item.get("index") or 0)),
                            ).fetchone()
                            if row:
                                asset_id = row["asset_id"]
                        _upsert_file(
                            conn,
                            torrent_id=torrent_id,
                            asset_id=asset_id,
                            item=item,
                            base_path=base_path,
                            save_path=save_path,
                            complete=complete,
                            cloud_uploaded_at=cloud,
                            report=report,
                            check_exists=check_exists,
                            moving=moving,
                            content_path=torrent.get("content_path"),
                            category=category,
                            archive_root=archive_root,
                        )
                        report.files_written += 1
                except Exception as exc:
                    report.errors.append(f"{info_hash[:12]}: {exc}")

            # qBittorrent manda: si su regla borra un torrent ondemand, el
            # catálogo cierra el compromiso y deja el .strm para re-descargar.
            seen_hashes = {
                (torrent.get("hash") or "").lower()
                for torrent in torrents
            }
            for row in conn.execute(
                "SELECT * FROM torrents "
                "WHERE state <> 'removed' AND category = 'ondemand'"
            ).fetchall():
                info_hash = (row["info_hash"] or "").lower()
                if not info_hash or info_hash in seen_hashes:
                    continue
                conn.execute(
                    "UPDATE torrents SET state='removed', "
                    "qbit_state='removed', updated_at="
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (int(row["id"]),),
                )
                files = conn.execute(
                    "SELECT id, local_path, library_path, storage_location "
                    "FROM torrent_files WHERE torrent_id = ?",
                    (int(row["id"]),),
                ).fetchall()
                for file_row in files:
                    paths = [
                        path
                        for path in (
                            file_row["local_path"],
                            file_row["library_path"],
                        )
                        if path and Path(path).is_file()
                    ]
                    if paths:
                        # El torrent desapareció, pero el fichero sigue en
                        # archivo/disco: se conserva para reproducir.
                        keep = paths[0]
                        created = "archived" if (
                            file_row["storage_location"] == "1FICHIER"
                        ) else "ready"
                        if file_row["storage_location"] == "1FICHIER":
                            local, library = None, keep
                        else:
                            local, library = keep, None
                        conn.execute(
                            "UPDATE torrent_files SET state=?, local_path=?, "
                            "library_path=?, updated_at="
                            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                            (created, local, library, int(file_row["id"])),
                        )
                    else:
                        conn.execute(
                            "UPDATE torrent_files SET state='evicted', "
                            "local_path=NULL, library_path=NULL, updated_at="
                            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                            "WHERE id = ?",
                            (int(file_row["id"]),),
                        )
                commitments = conn.execute(
                    "SELECT * FROM seed_commitments "
                    "WHERE state IN ('approved','active','seeding') "
                    "AND (info_hash = ? OR "
                    "(? IS NOT NULL AND release_id = ?))",
                    (
                        row["info_hash"],
                        row["release_id"],
                        row["release_id"],
                    ),
                ).fetchall()
                for commitment in commitments:
                    policy = get_policy(conn, commitment["tracker"])
                    started = (
                        row["seed_started_at"] or commitment["started_at"]
                    )
                    early = True
                    if policy and started:
                        try:
                            start = datetime.fromisoformat(
                                str(started).replace("Z", "+00:00")
                            )
                            if current >= start + timedelta(
                                hours=policy.min_seed_hours
                            ):
                                early = False
                        except ValueError:
                            pass
                    if early:
                        conn.execute(
                            "UPDATE seed_commitments SET state='approved', "
                            "reason='reseed_early_removal', started_at=NULL, "
                            "completed_at=NULL WHERE id = ?",
                            (int(commitment["id"]),),
                        )
                        report.reseed_required += 1
                    else:
                        conn.execute(
                            "UPDATE seed_commitments SET state='done', "
                            "completed_at=? WHERE id = ?",
                            (current.isoformat(), int(commitment["id"])),
                        )
                report.torrents_removed += 1

            # Recalcular estado/puntero de los media tocados.
            for row in conn.execute(
                "SELECT DISTINCT asset_id FROM torrent_files WHERE asset_id IS NOT NULL"
            ).fetchall():
                status, active = _choose_active(
                    conn, int(row["asset_id"]), archive_root
                )
                conn.execute(
                    "UPDATE media_items SET status = ?, active_file_id = ?, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?",
                    (status, active, int(row["asset_id"])),
                )
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    return report
