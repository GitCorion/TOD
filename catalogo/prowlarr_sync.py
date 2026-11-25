"""Ingesta de candidatos desde Prowlarr.

Solo crea filas `releases` (source=prowlarr). La privacidad se toma del
indexer, no se asume privada: Prowlarr conoce el `privacy` de cada uno.
No descarga .torrent ni datos, y no toca qBittorrent.
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import connect
from .importer import norm_title
from .policy import reconcile_privacy
from .public_sync import audio_of, matches, quality_of, year_of
from .release_parser import parse_release_info

PRIVATE_CATEGORIES = ("2000", "5000")


def _select_assets(
    db_path: str, tracker: str, key: str | None, limit: int,
    min_year: int = 0, include_existing: bool = False,
) -> list[Any]:
    with closing(connect(db_path)) as conn:
        if key:
            return conn.execute(
                "SELECT * FROM media_items WHERE key=? OR media_id=? LIMIT ?",
                (key, key, limit),
            ).fetchall()
        if include_existing:
            return conn.execute(
                """
                SELECT a.* FROM media_items a
                WHERE a.status = 'COLD'
                  AND (a.year IS NULL OR a.year >= ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM torrent_files f WHERE f.asset_id = a.id
                  )
                ORDER BY a.year DESC, a.id
                LIMIT ?
                """,
                (min_year, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT a.* FROM media_items a
            WHERE a.status = 'COLD'
              AND (a.year IS NULL OR a.year >= ?)
              AND NOT EXISTS (
                  SELECT 1 FROM torrent_files f WHERE f.asset_id = a.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM releases r
                  WHERE r.asset_id = a.id AND r.tracker = ?
              )
            ORDER BY a.year DESC, a.id
            LIMIT ?
            """,
            (min_year, tracker, limit),
        ).fetchall()


def _upsert(
    conn, asset: Any, tracker: str, result: dict, now: str,
    *, privacy: str = "private",
) -> str:
    title = result.get("title") or ""
    size = int(result.get("size") or 0)
    guid = result.get("guid") or result.get("infoUrl") or title
    guid = f"prowlarr:{tracker}:{guid}"
    existing = conn.execute(
        """
        SELECT id FROM releases
        WHERE asset_id=? AND tracker=? AND title=? AND size_bytes=?
        LIMIT 1
        """,
        (int(asset["id"]), tracker, title, size),
    ).fetchone()
    parsed = parse_release_info(title)
    values = (
        tracker,
        title,
        norm_title(title),
        year_of(title),
        parsed.quality,
        parsed.audio,
        *parsed.extra_db_tuple(),
        size,
        (result.get("infoHash") or "").lower() or None,
        result.get("downloadUrl"),
        int(result.get("seeders") or 0),
        int(result.get("peers") or 0),
        "alive",
        now,
    )
    if existing:
        conn.execute(
            """
            UPDATE releases SET tracker=?, title=?, normalized_title=?, year=?,
                quality=?, audio=?,
                source_type=?, codec=?, hdr=?, audio_codec=?, audio_channels=?,
                edition=?, release_group=?, proper_rank=?, streaming_service=?,
                parsed_flags=?,
                size_bytes=?, info_hash=?, download_url=?,
                seeders=?, peers=?, health=?, health_checked_at=?,
                source='prowlarr', privacy=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id=?
            """,
            values + (privacy, int(existing["id"]),),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO releases(asset_id, tracker, guid, title, normalized_title,
                             year, quality, audio,
                             source_type, codec, hdr, audio_codec, audio_channels,
                             edition, release_group, proper_rank, streaming_service,
                             parsed_flags,
                             size_bytes, is_pack,
                             info_hash, download_url, seeders, peers, health,
                             health_checked_at, source, privacy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 'prowlarr', ?)
        ON CONFLICT(guid) DO UPDATE SET
            tracker=excluded.tracker, title=excluded.title,
            normalized_title=excluded.normalized_title, year=excluded.year,
            quality=excluded.quality, audio=excluded.audio,
            source_type=excluded.source_type, codec=excluded.codec,
            hdr=excluded.hdr, audio_codec=excluded.audio_codec,
            audio_channels=excluded.audio_channels, edition=excluded.edition,
            release_group=excluded.release_group, proper_rank=excluded.proper_rank,
            streaming_service=excluded.streaming_service,
            parsed_flags=excluded.parsed_flags,
            size_bytes=excluded.size_bytes, info_hash=excluded.info_hash,
            download_url=excluded.download_url, seeders=excluded.seeders,
            peers=excluded.peers, health=excluded.health,
            health_checked_at=excluded.health_checked_at,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (int(asset["id"]),) + values[:1] + (guid,) + values[1:] + (privacy,),
    )
    return "inserted"


def sync_private(
    db_path: str,
    client: Any,
    *,
    tracker: str,
    indexer_id: int,
    key: str | None = None,
    limit: int = 10,
    delay: float = 1.0,
    min_seeders: int = 1,
    min_year: int = 0,
    include_existing: bool = False,
    dry_run: bool = False,
    privacy: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    if privacy is None:
        privacy = "private"
        try:
            for info in client.indexers():
                if int(info.get("id") or 0) == int(indexer_id):
                    privacy = (
                        "public"
                        if str(info.get("privacy") or "").lower() == "public"
                        else "private"
                    )
                    break
        except Exception:
            pass
    report: dict[str, Any] = {
        "tracker": tracker,
        "indexer_id": indexer_id,
        "assets_checked": 0,
        "results": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    assets = _select_assets(
        db_path, tracker, key, limit, min_year, include_existing
    )
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for index, asset in enumerate(assets):
                report["assets_checked"] += 1
                query = " ".join(
                    part for part in (
                        asset["title"] or asset["media_id"],
                        str(asset["year"]) if asset["year"] else "",
                    ) if part
                )
                try:
                    results = client.search(
                        query, media_type=asset["kind"],
                        indexer_ids=[indexer_id], limit=50,
                    )
                except Exception as exc:
                    report["errors"].append(f"{asset['key']}: {exc}")
                    continue
                report["results"] += len(results)
                accepted = [
                    item for item in results
                    if int(item.get("seeders") or 0) >= min_seeders
                    and matches(asset, item)
                ]
                for item in accepted[:50]:
                    if dry_run:
                        report["inserted"] += 1
                        continue
                    outcome = _upsert(
                        conn, asset, tracker, item, now, privacy=privacy
                    )
                    report[outcome] += 1
                if delay and index < len(assets) - 1:
                    time.sleep(delay)
            if dry_run:
                conn.rollback()
            else:
                reconcile_privacy(conn, tracker, privacy)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    return report
