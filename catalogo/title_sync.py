"""Alta masiva de títulos en el catálogo.

No descarga nada ni busca releases: solo mantiene la lista de títulos que la
app puede ofrecer. La búsqueda de fuentes vive en `bulk_sync`.
"""

from __future__ import annotations

import json
from contextlib import closing

from .db import connect


def _aliases_json(value) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(list(value), ensure_ascii=False)


def upsert_movies(db_path, items, *, source: str = "tmdb") -> dict:
    """Inserta o actualiza películas por `movie:tmdb:<id>`."""
    inserted = 0
    updated = 0
    skipped = 0
    with closing(connect(db_path)) as conn:
        for item in items:
            try:
                tmdb = int(item.get("tmdb") or 0)
            except (TypeError, ValueError):
                tmdb = 0
            title = (item.get("title") or "").strip()
            if not tmdb or not title:
                skipped += 1
                continue
            key = f"movie:tmdb:{tmdb}"
            existing = conn.execute(
                "SELECT id FROM media_items WHERE key = ?", (key,)
            ).fetchone()
            aliases = _aliases_json(item.get("aliases"))
            if existing:
                conn.execute(
                    """
                    UPDATE media_items SET
                        title = ?,
                        original_title = COALESCE(?, original_title),
                        year = COALESCE(?, year),
                        release_date = COALESCE(?, release_date),
                        original_language = COALESCE(?, original_language),
                        original_country = COALESCE(?, original_country),
                        aliases = COALESCE(?, aliases),
                        popularity = COALESCE(?, popularity),
                        poster_path = COALESCE(?, poster_path),
                        backdrop_path = COALESCE(?, backdrop_path),
                        overview = COALESCE(?, overview),
                        vote_average = COALESCE(?, vote_average),
                        external_source = COALESCE(external_source, ?),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (
                        title,
                        item.get("original_title"),
                        item.get("year"),
                        item.get("release_date"),
                        item.get("original_language"),
                        item.get("original_country"),
                        aliases,
                        item.get("popularity"),
                        item.get("poster_path"),
                        item.get("backdrop_path"),
                        item.get("overview"),
                        item.get("vote_average"),
                        source,
                        int(existing["id"]),
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO media_items(
                        key, kind, tmdb, title, year, release_date,
                        original_title, original_language, original_country,
                        aliases, popularity,
                        poster_path, backdrop_path, overview, vote_average,
                        external_source
                    ) VALUES(?, 'movie', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        tmdb,
                        title,
                        item.get("year"),
                        item.get("release_date"),
                        item.get("original_title"),
                        item.get("original_language"),
                        item.get("original_country"),
                        aliases,
                        item.get("popularity"),
                        item.get("poster_path"),
                        item.get("backdrop_path"),
                        item.get("overview"),
                        item.get("vote_average"),
                        source,
                    ),
                )
                inserted += 1
        conn.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "source": source,
    }


def upsert_episodes(db_path, series, episodes, *, source: str = "tmdb") -> dict:
    """Inserta o actualiza episodios de una serie por su clave estable."""
    try:
        tmdb = int(series.get("tmdb") or 0)
    except (TypeError, ValueError):
        tmdb = 0
    title = (series.get("title") or "").strip()
    if not tmdb or not title:
        return {"inserted": 0, "updated": 0, "skipped": len(episodes), "source": source}
    inserted = 0
    updated = 0
    skipped = 0
    with closing(connect(db_path)) as conn:
        for item in episodes:
            try:
                season = int(item.get("season") or 0)
                episode = int(item.get("episode") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if season <= 0 or episode <= 0:
                skipped += 1
                continue
            key = f"series:tmdb:{tmdb}:{season}:{episode}"
            existing = conn.execute(
                "SELECT id FROM media_items WHERE key = ?", (key,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE media_items SET
                        title = ?,
                        episode_title = COALESCE(?, episode_title),
                        episode_overview = COALESCE(?, episode_overview),
                        original_title = COALESCE(?, original_title),
                        year = COALESCE(?, year),
                        season = ?, episode = ?,
                        air_date = COALESCE(?, air_date),
                        original_language = COALESCE(?, original_language),
                        original_country = COALESCE(?, original_country),
                        popularity = COALESCE(?, popularity),
                        poster_path = COALESCE(?, poster_path),
                        backdrop_path = COALESCE(?, backdrop_path),
                        overview = COALESCE(?, overview),
                        vote_average = COALESCE(?, vote_average),
                        external_source = COALESCE(external_source, ?),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (
                        title,
                        item.get("name"),
                        item.get("overview"),
                        series.get("original_title"),
                        series.get("year"),
                        season,
                        episode,
                        item.get("air_date"),
                        series.get("original_language"),
                        series.get("original_country"),
                        series.get("popularity"),
                        series.get("poster_path"),
                        series.get("backdrop_path"),
                        series.get("overview"),
                        series.get("vote_average"),
                        source,
                        int(existing["id"]),
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO media_items(
                        key, kind, tmdb, title, episode_title, episode_overview,
                        year, season, episode, air_date,
                        original_title, original_language, original_country,
                        popularity,
                        poster_path, backdrop_path, overview, vote_average,
                        external_source
                    ) VALUES(?, 'series', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        tmdb,
                        title,
                        item.get("name"),
                        item.get("overview"),
                        series.get("year"),
                        season,
                        episode,
                        item.get("air_date"),
                        series.get("original_title"),
                        series.get("original_language"),
                        series.get("original_country"),
                        series.get("popularity"),
                        series.get("poster_path"),
                        series.get("backdrop_path"),
                        series.get("overview"),
                        series.get("vote_average"),
                        source,
                    ),
                )
                inserted += 1
        conn.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "source": source,
    }
