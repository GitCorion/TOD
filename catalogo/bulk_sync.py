"""Captura de releases para títulos ya catalogados.

Una búsqueda a Prowlarr por título contra los indexers indicados (por defecto
los españoles de cine/series). No usa torrents ni qBittorrent: solo guarda
candidatos. Va despacio a propósito para no saturar los trackers.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Iterable

from .db import connect
from .importer import norm_title
from .jackett import sanitize_link
from .public_sync import quality_of, year_of
from .trackers import (
    GENERAL_TRACKERS,
    audio_for,
    spanish_trackers_from_indexers,
)


def _aliases(asset) -> list[str]:
    if "aliases" not in asset.keys():
        return []
    try:
        value = json.loads(asset["aliases"] or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _norm_candidates(asset) -> list[str]:
    values = [asset["original_title"], asset["title"]]
    values.extend(_aliases(asset))
    result: list[str] = []
    for value in values:
        normalized = norm_title(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


SXXEYY = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
XXYY = re.compile(r"(?i)\b(\d{1,2})x(\d{2,3})\b")
SEASON = re.compile(r"(?i)\bS(\d{1,2})\b")
SEASON_WORD = re.compile(r"(?i)\b(?:temporada|season)\s*(\d{1,2})\b")
PACK = re.compile(r"(?i)\b(?:pack|complete|completa|temporada|season)\b")


def parse_episode(title: str | None) -> tuple[int, int] | None:
    match = SXXEYY.search(title or "") or XXYY.search(title or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def season_of(title: str | None) -> int | None:
    text = title or ""
    match = SEASON.search(text) or SEASON_WORD.search(text)
    return int(match.group(1)) if match else None


def matches_asset(asset, result: dict, kind: str = "movie") -> bool:
    """Valida que un resultado de Prowlarr corresponde al título."""
    asset_tmdb = asset["tmdb"] if "tmdb" in asset.keys() else None
    result_tmdb = result.get("tmdbId") or 0
    tmdb_ok = False
    if asset_tmdb and result_tmdb:
        if int(asset_tmdb) != int(result_tmdb):
            return False
        tmdb_ok = True

    result_norm = norm_title(result.get("title"))
    if not result_norm:
        return False
    if not tmdb_ok:
        candidates = _norm_candidates(asset)
        if not candidates:
            return False
        hit = False
        for candidate in candidates:
            if len(candidate) < 5:
                if result_norm == candidate:
                    hit = True
                    break
            elif candidate in result_norm or result_norm in candidate:
                hit = True
                break
        if not hit:
            return False

    year = asset["year"] if "year" in asset.keys() else None
    if year and not tmdb_ok:
        result_year = year_of(result.get("title"))
        if result_year and abs(int(result_year) - int(year)) > 1:
            return False

    if kind == "series":
        season = int(asset["season"] or 0)
        episode = int(asset["episode"] or 0)
        parsed = parse_episode(result.get("title"))
        if parsed is not None:
            return parsed == (season, episode)
        result_season = season_of(result.get("title"))
        return result_season == season

    return True


def _query_title(value: str | None, year: int | None) -> str:
    """Quita un año final duplicado del título legado."""
    text = (value or "").strip()
    if year:
        text = re.sub(rf"\s*\(?{int(year)}\)?\s*$", "", text).strip()
    return text


def build_query(asset, kind: str = "movie") -> str:
    year = asset["year"] if "year" in asset.keys() else None
    title = _query_title(asset["original_title"] or asset["title"], year)
    return f"{title} {year}".strip() if year else title


def local_query(asset, kind: str = "movie") -> str:
    year = asset["year"] if "year" in asset.keys() else None
    title = _query_title(asset["title"], year)
    original = _query_title(asset["original_title"], year)
    if title == original:
        return ""
    return f"{title} {year}".strip() if year else title


def _select_assets(
    db_path,
    *,
    trackers: list[str],
    year: int | None,
    key: str | None,
    limit: int,
    kind: str = "movie",
    recheck: bool = False,
) -> list:
    query = """
        SELECT a.* FROM media_items a
        WHERE a.kind = ?
          AND a.status = 'COLD'
          AND NOT EXISTS (
              SELECT 1 FROM torrent_files f WHERE f.asset_id = a.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM library_files lf
              WHERE lf.kind = a.kind AND lf.tmdb = a.tmdb
                AND (
                    a.kind = 'movie'
                    OR (lf.season = a.season AND lf.episode = a.episode)
                )
          )
    """
    params: list[object] = [kind]
    if year:
        query += " AND a.year = ?"
        params.append(year)
    if key:
        query += " AND (a.key = ? OR a.media_id = ?)"
        params.extend([key, key])
    if trackers and not recheck:
        placeholders = ",".join("?" for _ in trackers)
        query += (
            " AND NOT EXISTS ("
            "  SELECT 1 FROM releases r"
            "  WHERE r.asset_id = a.id AND r.tracker IN ("
            f"    {placeholders}"
            "  )"
            ")"
        )
        params.extend(trackers)
    query += " ORDER BY COALESCE(a.popularity, 0) DESC, a.id DESC LIMIT ?"
    params.append(limit)
    with closing(connect(db_path)) as conn:
        return conn.execute(query, params).fetchall()


def resolve_indexers(
    client, names, *, indexers: list[dict] | None = None
) -> tuple[list[tuple[str, int]], list[str]]:
    payload = indexers if indexers is not None else client.indexers()
    by_name = {
        str(item.get("name")): int(item.get("id"))
        for item in payload
        if item.get("name")
        and item.get("id") is not None
        and item.get("enable") is not False
    }
    if not names:
        # Sin lista explícita, usa todos los indexers habilitados de Prowlarr.
        return [(name, indexer_id) for name, indexer_id in by_name.items()], []
    found: list[tuple[str, int]] = []
    missing: list[str] = []
    for name in names:
        if name in by_name:
            found.append((name, by_name[name]))
        else:
            missing.append(name)
    return found, missing


def _upsert_release(
    conn,
    asset_id: int,
    tracker: str,
    result: dict,
    now: str,
    *,
    is_pack: bool = False,
    season: int | None = None,
    episode: int | None = None,
    spanish_trackers: Iterable[str] | None = None,
    privacy: str = "private",
) -> str:
    title = (result.get("title") or "").strip()
    size = int(result.get("size") or 0)
    if not title or size <= 0:
        return "skipped"
    info_hash = (result.get("infoHash") or "").lower() or None
    if info_hash:
        previous = conn.execute(
            "SELECT asset_id FROM releases WHERE info_hash = ?", (info_hash,)
        ).fetchone()
        if previous:
            if is_pack:
                # El pack se comparte entre episodios; el hash solo puede
                # quedar en una fila. Las demás lo resuelven al descargar.
                info_hash = None
            elif int(previous["asset_id"]) != asset_id:
                return "skipped"

    guid = f"prowlarr:{tracker}:{result.get('guid') or title}"
    if is_pack and season and episode:
        guid = f"{guid}:S{int(season):02d}E{int(episode):02d}"
    same_guid = conn.execute(
        "SELECT asset_id FROM releases WHERE guid = ?", (guid,)
    ).fetchone()
    if same_guid and int(same_guid["asset_id"]) != asset_id:
        return "skipped"

    audio, confidence = audio_for(
        title, tracker, spanish_trackers=spanish_trackers
    )
    values = (
        tracker,
        title,
        norm_title(title),
        year_of(title),
        quality_of(title),
        audio,
        confidence,
        size,
        1 if is_pack else 0,
        info_hash,
        result.get("downloadUrl"),
        result.get("infoUrl") or result.get("info_url"),
        int(result.get("seeders") or 0),
        int(result.get("peers") or 0),
        now,
        privacy,
    )
    existing = conn.execute(
        """
        SELECT id FROM releases
        WHERE asset_id = ? AND tracker = ? AND title = ? AND size_bytes = ?
        LIMIT 1
        """,
        (asset_id, tracker, title, size),
    ).fetchone()
    if existing is None:
        new_norm = norm_title(title)
        candidates = conn.execute(
            """
            SELECT id, title, normalized_title FROM releases
            WHERE asset_id = ? AND tracker = ?
              AND size_bytes BETWEEN ? AND ?
            """,
            (asset_id, tracker, int(size * 0.98), int(size * 1.02)),
        ).fetchall()
        for candidate in candidates:
            old_norm = candidate["normalized_title"] or norm_title(
                candidate["title"]
            )
            if old_norm == new_norm or (
                len(old_norm) >= 16
                and len(new_norm) >= 16
                and (old_norm in new_norm or new_norm in old_norm)
            ):
                existing = candidate
                break
    if existing:
        conn.execute(
            """
            UPDATE releases SET tracker=?, title=?, normalized_title=?, year=?,
                quality=?, audio=?, audio_confidence=?, size_bytes=?, is_pack=?,
                info_hash=?, download_url=?, info_url=?, seeders=?, peers=?,
                health='alive', health_checked_at=?,
                source='prowlarr', privacy=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id=?
            """,
            values + (int(existing["id"]),),
        )
        return "updated"

    conn.execute(
        """
        INSERT INTO releases(
            asset_id, tracker, guid, title, normalized_title, year, quality,
            audio, audio_confidence, size_bytes, is_pack, info_hash,
            download_url, info_url, seeders, peers, health, health_checked_at,
            source, privacy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'alive',
                  ?, 'prowlarr', ?)
        """,
        (asset_id,) + values[:1] + (guid,) + values[1:],
    )
    return "inserted"


def _jackett_item(item: dict, tracker: str) -> dict:
    """Convierte un resultado de Jackett al formato de Prowlarr."""
    return {
        "title": item.get("title") or "",
        "size": int(item.get("size") or 0),
        "seeders": int(item.get("seeders") or 0),
        "peers": int(item.get("peers") or 0),
        "infoHash": (item.get("infohash") or "").lower() or None,
        "downloadUrl": sanitize_link(item.get("link") or ""),
        "infoUrl": item.get("infoUrl") or item.get("comments") or "",
        "guid": item.get("guid") or item.get("title") or "",
        "indexer": tracker,
        "tmdbId": int(item.get("tmdbId") or 0),
        "category": item.get("category") or "",
    }


def sync_sources(
    db_path,
    client,
    *,
    jackett=None,
    year: int | None = None,
    limit: int = 25,
    delay: float = 8.0,
    trackers: tuple[str, ...] = GENERAL_TRACKERS,
    min_seeders: int = 1,
    key: str | None = None,
    kind: str = "movie",
    dry_run: bool = False,
    now: datetime | None = None,
    recheck: bool = False,
    query_override: str | None = None,
    accept_unmatched: bool = False,
) -> dict:
    indexer_payload = client.indexers()
    found, missing = resolve_indexers(
        client, trackers, indexers=indexer_payload
    )
    if not found and jackett is None:
        raise ValueError("ningún indexer disponible en Prowlarr")
    indexer_ids = [indexer_id for _, indexer_id in found]
    found_names = [name for name, _ in found]
    privacy_by_name = {
        str(item.get("name") or ""): (
            "public"
            if str(item.get("privacy") or "").lower() == "public"
            else "private"
        )
        for item in indexer_payload
        if item.get("name")
    }
    spanish_trackers = spanish_trackers_from_indexers(indexer_payload)
    # Jackett se consulta en "all": cada resultado trae su indexer real.
    jackett_sources = [("all", "Jackett")] if jackett else []
    missing = list(missing)
    current = now or datetime.now(timezone.utc)
    checked_at = current.isoformat()
    media_type = "series" if kind == "series" else "movie"
    assets = _select_assets(
        db_path,
        trackers=found_names,
        year=year,
        key=key,
        limit=limit,
        kind=kind,
        recheck=recheck,
    )
    report: dict = {
        "assets_checked": 0,
        "queries": 0,
        "results": 0,
        "jackett_results": 0,
        "accepted": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "items": [],
        "indexers": found,
        "jackett_indexers": [tracker for _, tracker in jackett_sources],
        "missing_indexers": missing,
        "delay": delay,
        "dry_run": dry_run,
    }
    # Las búsquedas son la parte lenta. Se hacen fuera de la transacción y
    # se guardan por lotes: así la base nunca queda bloqueada mientras
    # Prowlarr/Jackett responden.
    batch_size = 25
    for batch_start in range(0, len(assets), batch_size):
        batch = assets[batch_start:batch_start + batch_size]
        pending: list[tuple[dict, list[dict]]] = []
        for offset, asset in enumerate(batch):
            index = batch_start + offset
            report["assets_checked"] += 1
            queries = []
            if query_override:
                queries = [str(query_override).strip()]
            elif kind == "series":
                first = local_query(asset) or build_query(asset)
                if first:
                    queries.append(first)
                other = build_query(asset)
                if other and other not in queries:
                    queries.append(other)
            else:
                primary = local_query(asset) or build_query(asset)
                if primary:
                    queries.append(primary)
                extra = build_query(asset)
                if extra and extra not in queries:
                    queries.append(extra)
            accepted: list[dict] = []
            asset_results = 0
            for query in queries:
                report["queries"] += 1
                results: list[dict] = []
                try:
                    results.extend(
                        client.search(
                            query,
                            media_type=media_type,
                            indexer_ids=indexer_ids,
                            limit=50,
                        )
                    )
                except Exception as exc:
                    report["errors"].append(f"{asset['key']}: {exc}")
                for slug, tracker in jackett_sources:
                    try:
                        found_results = jackett.search(
                            slug, query, media_type=media_type, limit=50
                        )
                    except Exception as exc:
                        report["errors"].append(
                            f"{asset['key']} [{slug}]: {exc}"
                        )
                        continue
                    report["jackett_results"] += len(found_results)
                    results.extend(
                        _jackett_item(
                            item,
                            item.get("indexer") or tracker,
                        )
                        for item in found_results
                    )
                asset_results += len(results)
                report["results"] += len(results)
                matched = [
                    item for item in results
                    if int(item.get("seeders") or 0) >= min_seeders
                    and (accept_unmatched or matches_asset(asset, item, kind=kind))
                ]
                if accept_unmatched:
                    accepted = matched
                elif kind == "series":
                    exact = [
                        item for item in matched
                        if parse_episode(item.get("title"))
                        == (
                            int(asset["season"] or 0),
                            int(asset["episode"] or 0),
                        )
                    ]
                    accepted = exact or matched
                else:
                    accepted = matched
                if accepted:
                    break
            report["items"].append(
                {
                    "key": asset["key"],
                    "query": queries[0] if queries else "",
                    "results": asset_results,
                    "accepted": len(accepted),
                }
            )
            report["accepted"] += len(accepted)
            pending.append((asset, accepted))
            if delay and index < len(assets) - 1:
                time.sleep(delay)

        if dry_run or not pending:
            continue
        with closing(connect(db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for asset, accepted in pending:
                    for item in accepted:
                        is_pack = kind == "series" and parse_episode(
                            item.get("title")
                        ) is None
                        outcome = _upsert_release(
                            conn,
                            int(asset["id"]),
                            item.get("indexer") or "",
                            item,
                            checked_at,
                            is_pack=is_pack,
                            season=(
                                int(asset["season"] or 0)
                                if kind == "series" else None
                            ),
                            episode=(
                                int(asset["episode"] or 0)
                                if kind == "series" else None
                            ),
                            spanish_trackers=spanish_trackers,
                            privacy=privacy_by_name.get(
                                item.get("indexer") or "", "private"
                            ),
                        )
                        report[outcome] += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    return report
