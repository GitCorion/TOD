"""Fallback público: DivxTotal y DonTorrent vía Jackett.

Inserta candidatos con `privacy='public'` y `source='jackett'`.
No descarga .torrent y no guarda la API key de Jackett en `download_url`.
"""

from __future__ import annotations

import re
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import connect
from .importer import norm_title
from .jackett import sanitize_link
from .release_parser import (
    audio_of as _rich_audio_of,
    parse_release_info,
    quality_of as _rich_quality_of,
)

PUBLIC_INDEXERS = {
    "divxtotal": "DivxTotal",
    "dontorrent": "DonTorrent",
}
QUALITY = re.compile(r"(?i)(2160p|4k|uhd|1080p|720p|480p)")
CAST = re.compile(r"(?i)\b(castellano|espa[ñn]ol|espanol|spanish|spa|esp|es)\b")
LATINO = re.compile(r"(?i)\b(latino|latam|lat)\b")
VOSE = re.compile(r"(?i)\b(vose|vos|subs?[ ._-]*(esp|spa|spanish))\b")
YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def quality_of(title: str) -> str:
    return _rich_quality_of(title)


def audio_of(title: str) -> str:
    return _rich_audio_of(title)


def year_of(title: str) -> int | None:
    # "Blade Runner 2049 (2017)" -> 2017, no 2049.
    matches = YEAR.findall(title or "")
    return int(matches[-1]) if matches else None


def matches(asset: Any, result: dict) -> bool:
    asset_title = norm_title(asset["title"] or asset["key"])
    result_norm = norm_title(result.get("title"))
    if not asset_title or not result_norm:
        return False
    asset_tmdb = asset["tmdb"] if "tmdb" in asset.keys() else None
    result_tmdb = result.get("tmdbId") or 0
    if asset_tmdb and result_tmdb:
        if int(asset_tmdb) != int(result_tmdb):
            return False
        # tmdb manda; el año del título puede ser parte del nombre.
        return True
    if asset_title not in result_norm and result_norm not in asset_title:
        return False
    if asset["year"]:
        year = year_of(result.get("title") or "")
        if year and year != int(asset["year"]):
            return False
    return True


def _select_assets(db_path, key: str | None, limit: int) -> list[Any]:
    with closing(connect(db_path)) as conn:
        if key:
            return conn.execute(
                "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT ?",
                (key, key, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM media_items a
            WHERE a.status = 'COLD'
              AND NOT EXISTS (
                  SELECT 1 FROM releases r
                  WHERE r.asset_id = a.id AND r.health = 'alive'
              )
            ORDER BY a.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def _upsert_release(conn, asset_id: int, tracker: str, result: dict,
                    now: str, torrent_bytes: bytes | None = None) -> str:
    link = sanitize_link(result.get("link") or "")
    if not link:
        return "skipped"
    guid = result.get("guid") or link
    guid = f"{tracker}:{guid}"
    title = result.get("title") or ""
    size = int(result.get("size") or 0)
    existing = conn.execute(
        """
        SELECT id FROM releases
        WHERE asset_id = ? AND tracker = ? AND title = ? AND size_bytes = ?
        LIMIT 1
        """,
        (asset_id, tracker, title, size),
    ).fetchone()
    info_hash = (result.get("infohash") or "").lower() or None
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
        info_hash,
        link,
        result.get("infoUrl") or result.get("info_url") or None,
        int(result.get("seeders") or 0),
        int(result.get("peers") or 0),
        "alive",
        now,
        "jackett",
        "public",
        torrent_bytes,
    )
    if existing:
        conn.execute(
            """
            UPDATE releases SET
                tracker = ?, title = ?, normalized_title = ?, year = ?,
                quality = ?, audio = ?,
                source_type = ?, codec = ?, hdr = ?, audio_codec = ?,
                audio_channels = ?, edition = ?, release_group = ?,
                proper_rank = ?, streaming_service = ?, parsed_flags = ?,
                size_bytes = ?, info_hash = ?,
                download_url = ?, info_url = ?, seeders = ?, peers = ?, health = ?,
                health_checked_at = ?, source = ?, privacy = ?,
                torrent_blob = COALESCE(?, torrent_blob),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            values + (int(existing["id"]),),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO releases(asset_id, tracker, guid, title, normalized_title,
                             year, quality, audio,
                             source_type, codec, hdr, audio_codec,
                             audio_channels, edition, release_group,
                             proper_rank, streaming_service, parsed_flags,
                             size_bytes, is_pack,
                             info_hash, download_url, info_url, seeders, peers,
                             health, health_checked_at, source, privacy,
                             torrent_blob)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guid) DO UPDATE SET
            tracker = excluded.tracker,
            title = excluded.title,
            normalized_title = excluded.normalized_title,
            year = excluded.year,
            quality = excluded.quality,
            audio = excluded.audio,
            source_type = excluded.source_type,
            codec = excluded.codec,
            hdr = excluded.hdr,
            audio_codec = excluded.audio_codec,
            audio_channels = excluded.audio_channels,
            edition = excluded.edition,
            release_group = excluded.release_group,
            proper_rank = excluded.proper_rank,
            streaming_service = excluded.streaming_service,
            parsed_flags = excluded.parsed_flags,
            size_bytes = excluded.size_bytes,
            info_hash = excluded.info_hash,
            download_url = excluded.download_url,
            seeders = excluded.seeders,
            peers = excluded.peers,
            health = excluded.health,
            health_checked_at = excluded.health_checked_at,
            source = excluded.source,
            privacy = excluded.privacy,
            torrent_blob = COALESCE(excluded.torrent_blob, releases.torrent_blob),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (asset_id,) + values[:1] + (guid,) + values[1:],
    )
    return "inserted"


def sync_assets(
    db_path: str,
    client: Any,
    *,
    key: str | None = None,
    limit: int = 10,
    indexers: Iterable[str] | None = None,
    dry_run: bool = False,
    delay: float = 1.0,
) -> dict[str, Any]:
    selected = list(indexers or ("all",))
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "assets_checked": 0,
        "results": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "by_indexer": {},
        "errors": [],
        "dry_run": dry_run,
    }
    assets = _select_assets(db_path, key, limit)
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for index, asset in enumerate(assets):
                report["assets_checked"] += 1
                title = asset["title"] or asset["media_id"] or ""
                year = str(asset["year"]) if asset["year"] else ""
                if year and re.search(rf"(?<!\d){year}(?!\d)", title):
                    year = ""
                query = " ".join(part for part in (title, year) if part)
                for indexer in selected:
                    try:
                        results = client.search(
                            indexer, query, media_type=asset["kind"]
                        )
                    except Exception as exc:
                        report["errors"].append(f"{indexer}: {exc}")
                        continue
                    report["results"] += len(results)
                    accepted = [
                        result for result in results
                        if int(result.get("seeders") or 0) > 0
                        and result.get("privacy") != "private"
                        and matches(asset, result)
                    ]
                    for result in accepted[:5]:
                        raw_tracker = result.get("indexer") or ""
                        tracker = (
                            PUBLIC_INDEXERS.get(raw_tracker)
                            or raw_tracker
                            or PUBLIC_INDEXERS.get(indexer)
                            or indexer
                        )
                        report["by_indexer"][tracker] = (
                            report["by_indexer"].get(tracker, 0) + 1
                        )
                        if dry_run:
                            report["inserted"] += 1
                            continue
                        blob = result.get("torrent")
                        if blob is None and hasattr(client, "fetch_torrent"):
                            try:
                                blob = client.fetch_torrent(result["link"])
                            except Exception as exc:
                                report["errors"].append(
                                    f"{indexer} .torrent: {exc}"
                                )
                        outcome = _upsert_release(
                            conn, int(asset["id"]), tracker, result, now,
                            torrent_bytes=blob,
                        )
                        report[outcome] += 1
                if delay and index < len(assets) - 1:
                    time.sleep(delay)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    return report
