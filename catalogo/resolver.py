"""Resolución de una clave estable al puntero actual.

No busca en Prowlarr, no habla con qBittorrent y no descarga nada.
Solo lee assets.db y decide de dónde saldría el vídeo:

    local_seed -> library (1fichier) -> torrent -> unavailable

play_targets puede forzar cualquiera de esos orígenes. Cambiarlo no cambia
la clave ni el .strm.
"""

from __future__ import annotations

import os
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import connect
from .identity import normalize_key

DEFAULT_MAX_STREAM_BYTES = 0


@dataclass(frozen=True)
class Resolution:
    key: str
    asset_id: int | None
    source: str
    reason: str
    path: str | None = None
    size: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    release_id: int | None = None
    file_id: int | None = None
    file_index: int | None = None
    tracker: str | None = None
    health: str | None = None
    info_hash: str | None = None
    state: str | None = None
    debrid_url: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "asset_id": self.asset_id,
            "source": self.source,
            "reason": self.reason,
            "size": self.size,
            "release_id": self.release_id,
            "file_id": self.file_id,
            "file_index": self.file_index,
            "tracker": self.tracker,
            "health": self.health,
            "has_local": bool(self.path),
            "has_debrid": bool(self.debrid_url),
            "etag": self.etag,
        }


def _etag(path: Path) -> tuple[str, str]:
    stat = path.stat()
    mtime = stat.st_mtime_ns
    return f'"{stat.st_size:x}-{mtime:x}"', f"{mtime}"


class Resolver:
    def __init__(
        self,
        db_path: Path | str,
        *,
        check_exists: bool = True,
        max_size_bytes: int | None = None,
    ):
        self.db_path = Path(db_path)
        self.check_exists = check_exists
        self.max_size_bytes = (
            DEFAULT_MAX_STREAM_BYTES if max_size_bytes is None else max_size_bytes
        )

    def _conn(self):
        return connect(self.db_path)

    def asset(self, key: str) -> Any:
        normalized = normalize_key(key) or key
        with closing(self._conn()) as conn:
            row = conn.execute(
                "SELECT * FROM media_items WHERE key = ?", (normalized,)
            ).fetchone()
            if row is None and normalized != key:
                row = conn.execute(
                    "SELECT * FROM media_items WHERE key = ?", (key,)
                ).fetchone()
            return row

    def _target(self, asset_id: int) -> Any:
        with closing(self._conn()) as conn:
            row = conn.execute(
                "SELECT * FROM play_targets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        # Los punteros heredados del import no son una elección manual:
        # mandan la regla por defecto (menor tamaño entre fuentes vivas).
        if row is not None and (row["note"] or "") == "imported":
            return None
        return row

    def _files(self, asset_id: int) -> list[Any]:
        with closing(self._conn()) as conn:
            return conn.execute(
                """
                SELECT f.*, t.state AS torrent_state, t.info_hash AS torrent_hash,
                       r.tracker AS release_tracker, r.health AS release_health
                FROM torrent_files f
                LEFT JOIN torrents t ON t.id = f.torrent_id
                LEFT JOIN releases r ON r.id = f.release_id
                WHERE f.asset_id = ?
                ORDER BY f.id
                """,
                (asset_id,),
            ).fetchall()

    def _best_release(self, asset_id: int) -> Any:
        with closing(self._conn()) as conn:
            return conn.execute(
                """
                SELECT releases.*
                FROM releases
                LEFT JOIN swarm_stats ss ON ss.release_id = releases.id
                WHERE releases.asset_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM stream_failures sf
                      WHERE sf.release_id = releases.id
                        AND sf.until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                  )
                ORDER BY
                    CASE WHEN ss.failures > 0 AND ss.started_ok = 0
                         THEN 1 ELSE 0 END,
                    CASE releases.health WHEN 'alive' THEN 0 WHEN 'unknown' THEN 1
                                WHEN 'stale' THEN 2 ELSE 3 END,
                    COALESCE(releases.size_bytes, 999999999999) ASC,
                    COALESCE(releases.seeders, 0) DESC,
                    releases.id DESC
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()

    def _alive_release(self, asset_id: int) -> Any:
        with closing(self._conn()) as conn:
            return conn.execute(
                """
                SELECT releases.*
                FROM releases
                LEFT JOIN swarm_stats ss ON ss.release_id = releases.id
                WHERE releases.asset_id = ? AND releases.health = 'alive'
                  AND NOT EXISTS (
                      SELECT 1 FROM stream_failures sf
                      WHERE sf.release_id = releases.id
                        AND sf.until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                  )
                ORDER BY
                    CASE WHEN ss.failures > 0 AND ss.started_ok = 0
                         THEN 1 ELSE 0 END,
                    COALESCE(releases.size_bytes, 999999999999) ASC,
                    COALESCE(releases.seeders, 0) DESC, releases.id DESC
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()

    def _release(self, release_id: int) -> Any:
        with closing(self._conn()) as conn:
            return conn.execute(
                "SELECT * FROM releases WHERE id = ?", (release_id,)
            ).fetchone()

    def _debrid_from_release(
        self, key: str, asset_id: int, release: Any, reason: str
    ) -> Resolution | None:
        if release is None or release["asset_id"] != asset_id:
            return None
        link = release["debrid_link"]
        if not release["debrid_cached"] or not link:
            return None
        return Resolution(
            key=key,
            asset_id=asset_id,
            source="debrid",
            reason=reason,
            size=release["size_bytes"],
            release_id=release["id"],
            tracker=release["tracker"],
            health=release["health"],
            debrid_url=link,
        )

    def _torrent_from_release(
        self, key: str, asset_id: int, release_id: int, reason: str
    ) -> Resolution | None:
        release = self._release(release_id)
        if release is None or release["asset_id"] != asset_id:
            return None
        return Resolution(
            key=key,
            asset_id=asset_id,
            source="torrent",
            reason=reason,
            size=release["size_bytes"],
            release_id=release["id"],
            tracker=release["tracker"],
            health=release["health"],
            info_hash=release["info_hash"],
        )

    def _local(self, key: str, asset_id: int, row: Any, source: str,
               path: str | None, reason: str) -> Resolution | None:
        if not path:
            return None
        if source == "local_seed":
            # Un fichero parcial tiene local_path, pero NO es reproducible:
            # hay que seguir por torrent y esperar piezas/buffer.
            file_state = (row["state"] or "").lower()
            if file_state not in ("ready", "archived", "seeding", "complete"):
                return None
        file_path = Path(path)
        if self.check_exists and not file_path.is_file():
            return None
        size = row["size_bytes"]
        etag = None
        last_modified = None
        try:
            if file_path.is_file():
                size = file_path.stat().st_size
                etag, last_modified = _etag(file_path)
        except OSError:
            if self.check_exists:
                return None
        return Resolution(
            key=key,
            asset_id=asset_id,
            source=source,
            reason=reason,
            path=str(file_path),
            size=size,
            etag=etag,
            last_modified=last_modified,
            release_id=row["release_id"],
            file_id=row["id"],
            file_index=row["file_index"],
            tracker=row["release_tracker"],
            health=row["release_health"],
            info_hash=row["torrent_hash"],
            state=row["state"],
        )

    def _library_for(self, asset: Any) -> Resolution | None:
        try:
            tmdb = int(asset["tmdb"] or 0)
        except (TypeError, ValueError):
            return None
        if not tmdb:
            return None
        with closing(self._conn()) as conn:
            if asset["kind"] == "movie":
                row = conn.execute(
                    "SELECT * FROM library_files WHERE kind='movie' "
                    "AND tmdb = ? LIMIT 1",
                    (tmdb,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM library_files WHERE kind='series' "
                    "AND tmdb = ? AND season = ? AND episode = ? LIMIT 1",
                    (
                        tmdb,
                        int(asset["season"] or 0),
                        int(asset["episode"] or 0),
                    ),
                ).fetchone()
        if row is None:
            return None
        path = Path(row["path"])
        if self.check_exists and not path.is_file():
            return None
        size = int(row["size_bytes"] or 0) or None
        etag = None
        last_modified = None
        try:
            if path.is_file():
                size = path.stat().st_size
                etag, last_modified = _etag(path)
        except OSError:
            if self.check_exists:
                return None
        return Resolution(
            key=asset["key"],
            asset_id=int(asset["id"]),
            source="library",
            reason="library_file",
            path=str(path),
            size=size,
            etag=etag,
            last_modified=last_modified,
        )

    def _torrent(
        self, key: str, asset_id: int, row: Any, reason: str
    ) -> Resolution:        return Resolution(
            key=key,
            asset_id=asset_id,
            source="torrent",
            reason=reason,
            size=row["size_bytes"] if row is not None else None,
            release_id=row["release_id"] if row is not None else None,
            file_id=row["id"] if row is not None else None,
            file_index=row["file_index"] if row is not None else None,
            tracker=row["release_tracker"] if row is not None else None,
            health=row["release_health"] if row is not None else None,
            info_hash=row["torrent_hash"] if row is not None else None,
            state=row["torrent_state"] if row is not None else None,
        )

    def sources(self, key: str) -> list[dict[str, Any]]:
        asset = self.asset(key)
        if asset is None:
            return []
        with closing(self._conn()) as conn:
            rows = conn.execute(
                """
                SELECT releases.id, releases.tracker, releases.title,
                       releases.quality, releases.audio, releases.size_bytes,
                       releases.seeders, releases.health, releases.privacy,
                       releases.source, releases.debrid_cached,
                       releases.debrid_link, ss.started_ok AS swarm_started_ok,
                       ss.failures AS swarm_failures,
                       ss.speed_bps AS swarm_speed_bps
                FROM releases
                LEFT JOIN swarm_stats ss ON ss.release_id = releases.id
                WHERE releases.asset_id = ?
                  AND releases.health IN ('alive', 'unknown', 'stale')
                  AND NOT EXISTS (
                      SELECT 1 FROM stream_failures sf
                      WHERE sf.release_id = releases.id
                        AND sf.until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                  )
                ORDER BY
                    CASE WHEN ss.failures > 0 AND ss.started_ok = 0
                         THEN 1 ELSE 0 END,
                    CASE releases.health WHEN 'alive' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,
                    COALESCE(releases.size_bytes, 999999999999) ASC,
                    COALESCE(releases.seeders, 0) DESC,
                    id DESC
                """,
                (asset["id"],),
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "release_id": row["id"],
                    "tracker": row["tracker"],
                    "title": row["title"],
                    "quality": row["quality"],
                    "audio": row["audio"],
                    "size_bytes": row["size_bytes"],
                    "seeders": row["seeders"],
                    "health": row["health"],
                    "privacy": row["privacy"],
                    "source": row["source"],
                    "too_large": bool(
                        self.max_size_bytes
                        and (row["size_bytes"] or 0) > self.max_size_bytes
                    ),
                    "has_debrid": bool(row["debrid_cached"] and row["debrid_link"]),
                    "swarm_started_ok": (
                        bool(row["swarm_started_ok"])
                        if row["swarm_started_ok"] is not None
                        else None
                    ),
                    "swarm_failures": row["swarm_failures"],
                    "swarm_speed_bps": row["swarm_speed_bps"],
                }
            )
        return result

    def resolve_release(self, key: str, release_id: int) -> Resolution:
        asset = self.asset(key)
        if asset is None:
            return Resolution(key=key, asset_id=None, source="unavailable",
                              reason="asset_not_found")
        release = self._release(release_id)
        if release is None or release["asset_id"] != asset["id"]:
            return Resolution(asset["key"], asset["id"], "unavailable",
                              "release_not_found")
        with closing(self._conn()) as conn:
            failed = conn.execute(
                """
                SELECT 1 FROM stream_failures
                WHERE release_id = ?
                  AND until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (int(release_id),),
            ).fetchone()
        if failed is not None:
            return Resolution(
                asset["key"], asset["id"], "unavailable",
                "stream_bad_swarm",
            )
        files = [
            row for row in self._files(asset["id"])
            if int(row["release_id"] or -1) == int(release_id)
        ]
        for row in files:
            if row["state"] in ("evicted", "missing"):
                continue
            if row["library_path"]:
                result = self._local(
                    asset["key"], asset["id"], row, "library",
                    row["library_path"], "requested_release",
                )
                if result is not None:
                    return result
            if row["local_path"]:
                result = self._local(
                    asset["key"], asset["id"], row, "local_seed",
                    row["local_path"], "requested_release",
                )
                if result is not None:
                    return result
        debrid = self._debrid_from_release(
            asset["key"], asset["id"], release, "requested_release"
        )
        if debrid is not None:
            return debrid
        return Resolution(
            key=asset["key"],
            asset_id=asset["id"],
            source="torrent",
            reason="requested_release",
            size=release["size_bytes"],
            release_id=release["id"],
            tracker=release["tracker"],
            health=release["health"],
            info_hash=release["info_hash"],
        )

    def resolve(self, key: str, release_id: int | None = None) -> Resolution:
        if release_id is not None:
            return self.resolve_release(key, release_id)
        asset = self.asset(key)
        if asset is None:
            return Resolution(key=key, asset_id=None, source="unavailable",
                              reason="asset_not_found")

        normalized = asset["key"]
        asset_id = asset["id"]
        library = self._library_for(asset)
        if library is not None:
            return library
        files = self._files(asset_id)
        target = self._target(asset_id)

        if target is not None and target["source_kind"] == "unavailable":
            return Resolution(normalized, asset_id, "unavailable",
                              target["note"] or "marked_unavailable")

        active_id = asset["active_file_id"]
        if active_id:
            active = next(
                (row for row in files if int(row["id"]) == int(active_id)),
                None,
            )
            if active is not None and active["state"] not in ("evicted", "missing"):
                if active["library_path"]:
                    result = self._local(
                        normalized, asset_id, active, "library",
                        active["library_path"], "active_file",
                    )
                    if result is not None:
                        return result
                if active["local_path"]:
                    result = self._local(
                        normalized, asset_id, active, "local_seed",
                        active["local_path"], "active_file",
                    )
                    if result is not None:
                        return result
                if active["torrent_id"] is not None:
                    return self._torrent(
                        normalized, asset_id, active, "active_file_torrent"
                    )

        if target is not None and target["source_kind"] != "auto":
            kind = target["source_kind"]
            if kind == "unavailable":
                return Resolution(normalized, asset_id, "unavailable",
                                  target["note"] or "marked_unavailable")
            if kind == "torrent":
                row = next(
                    (f for f in files
                     if target["file_id"] and f["id"] == target["file_id"]),
                    None,
                )
                if row is not None:
                    return self._torrent(
                        normalized, asset_id, row,
                        target["note"] or "target_torrent",
                    )
                if target["release_id"]:
                    release_row = self._release(target["release_id"])
                    debrid = self._debrid_from_release(
                        normalized, asset_id, release_row,
                        target["note"] or "target_debrid",
                    )
                    if debrid is not None:
                        return debrid
                    result = self._torrent_from_release(
                        normalized, asset_id, target["release_id"],
                        target["note"] or "target_torrent",
                    )
                    if result is not None:
                        if result.health == "alive":
                            return result
                        alive = self._alive_release(asset_id)
                        if alive is not None and alive["id"] != target["release_id"]:
                            fallback = self._torrent_from_release(
                                normalized, asset_id, alive["id"],
                                "fallback_alive_release",
                            )
                            if fallback is not None:
                                return fallback
                        if result.health not in ("dead", "blocked"):
                            return result
                reason_prefix = "target_invalid"
            else:
                row = next(
                    (f for f in files
                     if target["file_id"] and f["id"] == target["file_id"]),
                    None,
                )
                if row is not None:
                    path = (
                        row["library_path"] if kind == "library"
                        else row["local_path"]
                    )
                    result = self._local(
                        normalized, asset_id, row, kind, path,
                        target["note"] or f"target_{kind}",
                    )
                    if result is not None:
                        return result
                # El puntero apunta a algo que ya no sirve: se cae a auto.
                reason_prefix = "target_invalid"
        else:
            reason_prefix = "auto"

        # auto: local_seed primero (disco local, sin 1fichier), luego library.
        for source, column in (("local_seed", "local_path"), ("library", "library_path")):
            for row in files:
                if row["state"] in ("evicted", "missing"):
                    continue
                result = self._local(
                    normalized, asset_id, row, source, row[column],
                    f"{reason_prefix}_{source}",
                )
                if result is not None:
                    return result

        for row in files:
            if row["torrent_id"] is not None and row["torrent_state"] not in (
                "removed", "error"
            ):
                return self._torrent(
                    normalized, asset_id, row, f"{reason_prefix}_torrent"
                )

        release = self._best_release(asset_id)
        if release is not None and release["health"] in ("alive", "unknown", "stale"):
            debrid = self._debrid_from_release(
                normalized, asset_id, release, f"{reason_prefix}_debrid"
            )
            if debrid is not None:
                return debrid
            return Resolution(
                key=normalized,
                asset_id=asset_id,
                source="torrent",
                reason=f"{reason_prefix}_release",
                size=release["size_bytes"],
                release_id=release["id"],
                tracker=release["tracker"],
                health=release["health"],
            )

        return Resolution(normalized, asset_id, "unavailable",
                          "no_playable_source")

    def set_target(
        self,
        key: str,
        source_kind: str,
        *,
        release_id: int | None = None,
        file_id: int | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        allowed = {"auto", "library", "local_seed", "torrent", "unavailable"}
        if source_kind not in allowed:
            raise ValueError(f"source_kind inválido: {source_kind}")
        if source_kind in ("library", "local_seed") and file_id is None:
            raise ValueError(f"{source_kind} requiere file_id")
        if source_kind == "torrent" and file_id is None and release_id is None:
            raise ValueError("torrent requiere file_id o release_id")
        asset = self.asset(key)
        if asset is None:
            raise KeyError(f"asset no encontrado: {key}")

        if file_id is not None:
            with closing(self._conn()) as conn:
                file_row = conn.execute(
                    "SELECT * FROM torrent_files WHERE id = ? AND asset_id = ?",
                    (file_id, asset["id"]),
                ).fetchone()
            if file_row is None:
                raise KeyError(f"file_id {file_id} no pertenece a {key}")
            if release_id is None:
                release_id = file_row["release_id"]

        with closing(self._conn()) as conn:
            conn.execute(
                """
                INSERT INTO play_targets(asset_id, release_id, file_id,
                                          source_kind, note, updated_at)
                VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(asset_id) DO UPDATE SET
                    release_id = excluded.release_id,
                    file_id = excluded.file_id,
                    source_kind = excluded.source_kind,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (asset["id"], release_id, file_id, source_kind, note),
            )
            conn.commit()
        return {"key": asset["key"], "source_kind": source_kind,
                "release_id": release_id, "file_id": file_id}

    def targets(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as conn:
            rows = conn.execute(
                """
                SELECT a.key, p.source_kind, p.release_id, p.file_id,
                       p.note, p.updated_at
                FROM play_targets p
                JOIN media_items a ON a.id = p.asset_id
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with closing(self._conn()) as conn:
            return {
                "media_items": conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],
                "releases": conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0],
                "torrents": conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0],
                "torrent_files": conn.execute("SELECT COUNT(*) FROM torrent_files").fetchone()[0],
                "targets": conn.execute("SELECT COUNT(*) FROM play_targets").fetchone()[0],
                "seed_commitments": conn.execute("SELECT COUNT(*) FROM seed_commitments").fetchone()[0],
                "play_intents": conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0],
            }


def path_exists(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path)
