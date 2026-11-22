"""Importa la assets.db actual a los cimientos nuevos, sin destruir nada.

- Lee la base original en modo inmutable.
- Hace upsert por clave estable y por ids legacy.
- Nunca borra ni recrea la base destino.
- Rellena play_targets solo si el activo aún no tiene puntero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import connect, connect_legacy, migrate
from .identity import asset_key, normalize_key
from .title_sync import monitor_if_not_in_arr


def norm_title(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _select(conn, table: str, wanted: list[str]) -> str:
    available = _columns(conn, table)
    selected = [column for column in wanted if column in available]
    return ", ".join(selected)


@dataclass
class ImportReport:
    assets_seen: int = 0
    assets_inserted: int = 0
    assets_updated: int = 0
    needs_identity: int = 0
    releases_seen: int = 0
    releases_inserted: int = 0
    releases_skipped: int = 0
    files_seen: int = 0
    files_inserted: int = 0
    targets_inserted: int = 0
    skipped_assets: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "assets_seen": self.assets_seen,
            "assets_inserted": self.assets_inserted,
            "assets_updated": self.assets_updated,
            "needs_identity": self.needs_identity,
            "releases_seen": self.releases_seen,
            "releases_inserted": self.releases_inserted,
            "releases_skipped": self.releases_skipped,
            "files_seen": self.files_seen,
            "files_inserted": self.files_inserted,
            "targets_inserted": self.targets_inserted,
            "skipped_assets_sample": self.skipped_assets[:10],
        }


def _asset_key_for_legacy(row: Any) -> tuple[str, str] | None:
    old_key = row["key"]
    normalized = normalize_key(old_key)
    if normalized:
        status = "ok" if ":tmdb:" in normalized or ":imdb:" in normalized else "needs_identity"
        return normalized, status
    try:
        return asset_key(
            row["type"], row["tmdb"], row["season"], row["episode"], row["id"]
        )
    except ValueError:
        return None


def import_legacy(
    source_db: Path | str,
    dest_db: Path | str,
    migrations_dir: Path | str,
) -> ImportReport:
    report = ImportReport()
    source = connect_legacy(source_db)
    dest = connect(dest_db)
    try:
        migrate(dest, migrations_dir)
        dest_id_by_legacy: dict[int, int] = {}
        key_by_asset_id: dict[int, str] = {}

        assets_sql = _select(
            source,
            "assets",
            ["id", "key", "type", "tmdb", "season", "episode", "title", "year"],
        )
        for row in source.execute(f"SELECT {assets_sql} FROM assets"):
            report.assets_seen += 1
            result = _asset_key_for_legacy(row)
            if result is None:
                report.skipped_assets.append(f"{row['id']}:{row['key']}")
                continue
            key, status = result
            if status == "needs_identity":
                report.needs_identity += 1

            existing = dest.execute(
                "SELECT id FROM media_items WHERE key = ?", (key,)
            ).fetchone()
            if existing is None:
                cur = dest.execute(
                    """
                    INSERT INTO media_items(key, media_id, legacy_asset_id, kind,
                                            tmdb, imdb, season, episode, title,
                                            year, identity_status)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        key,
                        row["id"],
                        row["type"],
                        row["tmdb"],
                        row["season"],
                        row["episode"],
                        row["title"],
                        row["year"],
                        status,
                    ),
                )
                asset_id = cur.lastrowid
                report.assets_inserted += 1
                monitor_if_not_in_arr(dest, [key], source="legacy")
            else:
                asset_id = existing["id"]
                dest.execute(
                    """
                    UPDATE media_items SET
                        media_id = COALESCE(media_id, key),
                        legacy_asset_id = COALESCE(legacy_asset_id, ?),
                        identity_status = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (row["id"], status, asset_id),
                )
                report.assets_updated += 1
            dest_id_by_legacy[row["id"]] = asset_id
            key_by_asset_id[row["id"]] = key

        releases_sql = _select(
            source,
            "releases",
            [
                "id", "asset_id", "catalog_release_id", "tracker", "title",
                "download_url", "quality", "audio", "seeders", "size_bytes",
                "info_hash",
            ],
        )
        for row in source.execute(f"SELECT {releases_sql} FROM releases"):
            report.releases_seen += 1
            asset_id = dest_id_by_legacy.get(row["asset_id"])
            if asset_id is None:
                report.releases_skipped += 1
                continue
            before = dest.execute(
                "SELECT id FROM releases WHERE legacy_release_id = ?",
                (row["id"],),
            ).fetchone()
            try:
                dest.execute(
                    """
                    INSERT INTO releases(asset_id, legacy_release_id, tracker,
                                         title, normalized_title, year, quality,
                                         audio, size_bytes, is_pack, info_hash,
                                         download_url, seeders)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT(legacy_release_id) DO UPDATE SET
                        asset_id = excluded.asset_id,
                        tracker = excluded.tracker,
                        title = excluded.title,
                        normalized_title = excluded.normalized_title,
                        quality = excluded.quality,
                        audio = excluded.audio,
                        size_bytes = excluded.size_bytes,
                        info_hash = COALESCE(releases.info_hash, excluded.info_hash),
                        download_url = excluded.download_url,
                        seeders = excluded.seeders,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (
                        asset_id,
                        row["id"],
                        row["tracker"],
                        row["title"],
                        norm_title(row["title"]),
                        row["quality"],
                        row["audio"],
                        row["size_bytes"],
                        row["info_hash"],
                        row["download_url"],
                        row["seeders"],
                    ),
                )
                if before is None:
                    report.releases_inserted += 1
            except Exception:
                report.releases_skipped += 1

        files_sql = _select(
            source,
            "files",
            [
                "id", "asset_id", "release_id", "tier", "path", "qbit_hash",
                "qbit_file_index", "size_bytes", "quality", "audio", "state",
                "last_seen",
            ],
        )
        for row in source.execute(f"SELECT {files_sql} FROM files"):
            report.files_seen += 1
            asset_id = dest_id_by_legacy.get(row["asset_id"])
            if asset_id is None:
                continue

            release_id = None
            if row["release_id"] is not None:
                release_row = dest.execute(
                    "SELECT id FROM releases WHERE legacy_release_id = ?",
                    (row["release_id"],),
                ).fetchone()
                if release_row is not None:
                    release_id = release_row["id"]

            tier = (row["tier"] or "").lower()
            path = row["path"]
            local_path = path if tier in ("local_seed", "downloading") else None
            library_path = path if tier == "library" else None
            state = {
                "library": "archived",
                "local_seed": "ready",
                "downloading": "downloading",
            }.get(tier, "unknown")

            torrent_id = None
            if row["qbit_hash"]:
                torrent_state = "seeding" if tier == "local_seed" else "downloading"
                dest.execute(
                    """
                    INSERT INTO torrents(info_hash, release_id, state)
                    VALUES (?, ?, ?)
                    ON CONFLICT(info_hash) DO UPDATE SET
                        release_id = COALESCE(torrents.release_id, excluded.release_id),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (row["qbit_hash"], release_id, torrent_state),
                )
                torrent_id = dest.execute(
                    "SELECT id FROM torrents WHERE info_hash = ?",
                    (row["qbit_hash"],),
                ).fetchone()["id"]

            before = dest.execute(
                "SELECT id FROM torrent_files WHERE legacy_file_id = ?", (row["id"],)
            ).fetchone()
            storage = "1FICHIER" if library_path else ("LOCAL_DISK" if local_path else "NONE")
            dest.execute(
                """
                INSERT INTO torrent_files(asset_id, release_id, torrent_id,
                                          legacy_file_id, file_index, local_path,
                                          library_path, size_bytes, state, last_seen,
                                          storage_location)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(legacy_file_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    release_id = excluded.release_id,
                    torrent_id = excluded.torrent_id,
                    file_index = excluded.file_index,
                    local_path = excluded.local_path,
                    library_path = excluded.library_path,
                    size_bytes = excluded.size_bytes,
                    state = excluded.state,
                    last_seen = excluded.last_seen,
                    storage_location = excluded.storage_location,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    asset_id,
                    release_id,
                    torrent_id,
                    row["id"],
                    row["qbit_file_index"],
                    local_path,
                    library_path,
                    row["size_bytes"],
                    state,
                    row["last_seen"],
                    storage,
                ),
            )
            if before is None:
                report.files_inserted += 1

        # Puntero inicial: solo si no existe.
        for legacy_id, asset_id in dest_id_by_legacy.items():
            exists = dest.execute(
                "SELECT 1 FROM play_targets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if exists:
                continue
            library = dest.execute(
                """
                SELECT * FROM torrent_files WHERE asset_id = ? AND library_path IS NOT NULL
                ORDER BY id LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            local = dest.execute(
                """
                SELECT * FROM torrent_files WHERE asset_id = ? AND local_path IS NOT NULL
                ORDER BY id LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            release = dest.execute(
                "SELECT * FROM releases WHERE asset_id = ? ORDER BY COALESCE(seeders,0) DESC LIMIT 1",
                (asset_id,),
            ).fetchone()

            if local is not None:
                source_kind, file_id, release_id = "local_seed", local["id"], local["release_id"]
            elif library is not None:
                source_kind, file_id, release_id = "library", library["id"], library["release_id"]
            elif release is not None:
                source_kind, file_id, release_id = "torrent", None, release["id"]
            else:
                source_kind, file_id, release_id = "unavailable", None, None

            dest.execute(
                """
                INSERT INTO play_targets(asset_id, release_id, file_id,
                                          source_kind, note)
                VALUES (?, ?, ?, ?, 'imported')
                """,
                (asset_id, release_id, file_id, source_kind),
            )
            report.targets_inserted += 1

        dest.commit()
    finally:
        source.close()
        dest.close()
    return report
