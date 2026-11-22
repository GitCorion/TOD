"""Añadir: la recepción del catálogo.

Una entrada no descarga nada. Este módulo responde a tres preguntas:

- qué ha llegado (texto pegado, lista, CSV);
- si se puede identificar (TMDB) y si ya estaba;
- qué se hizo con cada línea, aunque fallara.

La importación añade identidad al catálogo, no cuerpos. El coste real sigue
viviendo en la reproducción; aquí solo se paga atención y alguna llamada a
TMDB.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import admin, jobs
from .config import Config
from .db import connect
from .title_sync import upsert_episodes
from .tmdb import TmdbClient, episode_items, movie_item, tv_item

MAX_LINES = 200
MAX_TORRENT_BYTES = 600_000
DEFAULT_KIND = "movie"
CACHE_TTL = 600.0

_TMDB_LIST = re.compile(
    r"themoviedb\.org/(?:[a-z]{2}(?:-[a-z]{2})?/)?list/(\d+)", re.I
)
_TMDB_MOVIE = re.compile(
    r"themoviedb\.org/(?:[a-z]{2}(?:-[a-z]{2})?/)?movie/(\d+)", re.I
)
_TMDB_TV = re.compile(
    r"themoviedb\.org/(?:[a-z]{2}(?:-[a-z]{2})?/)?tv/(\d+)"
    r"(?:/season/(\d+)(?:/episode/(\d+))?)?",
    re.I,
)
_KEY_MOVIE = re.compile(r"^movie:(tmdb|imdb|legacy):([A-Za-z0-9]+)$", re.I)
_KEY_SERIES = re.compile(
    r"^series:(tmdb|imdb|legacy):([A-Za-z0-9]+)(?::(\d+))?(?::(\d+))?$",
    re.I,
)
_BARE_ID = re.compile(r"^\d{1,9}$")

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str, int, int, int], tuple[float, dict[str, Any]]] = {}


class IntakeError(ValueError):
    """Error de entrada que la web puede contar al usuario."""


@dataclass(frozen=True)
class Entry:
    raw: str
    kind: str = "unknown"
    tmdb: int | None = None
    season: int | None = None
    episode: int | None = None
    list_id: int | None = None
    reason: str | None = None
    repeated: bool = False

    @property
    def key(self) -> str | None:
        if not self.tmdb:
            return None
        if self.kind == "movie":
            return f"movie:tmdb:{self.tmdb}"
        if self.kind == "series" and self.season and self.episode:
            return f"series:tmdb:{self.tmdb}:{self.season}:{self.episode}"
        if self.kind == "series" and self.season:
            return f"series:tmdb:{self.tmdb}:{self.season}"
        if self.kind == "series":
            return f"series:tmdb:{self.tmdb}"
        return None

    @property
    def identity(self) -> tuple[str, int, int, int]:
        return (
            self.kind,
            int(self.tmdb or self.list_id or 0),
            int(self.season or 0),
            int(self.episode or 0),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client(config: Config) -> TmdbClient:
    try:
        return admin._tmdb(config)
    except Exception as exc:
        raise IntakeError(
            "TMDB no está configurado. Añade la API key en Configuración → "
            "Integraciones para poder identificar los títulos."
        ) from exc


def _lines(text: str) -> list[str]:
    text = (text or "").replace("\ufeff", "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _csv_mode(lines: list[str]) -> bool:
    for line in lines:
        if line.startswith("#"):
            continue
        head = line.lower()
        return head.startswith("tmdb,") or head.startswith("id,kind")
    return False


def _entry_from_values(
    values: dict[str, Any], *, default_kind: str, raw: str
) -> Entry:
    tmdb = str(values.get("tmdb") or values.get("id") or "").strip()
    if not tmdb.isdigit():
        return Entry(raw=raw, reason="Falta un ID de TMDB válido")
    kind = str(values.get("kind") or values.get("type") or default_kind).strip().lower()
    if kind not in ("movie", "series"):
        return Entry(raw=raw, reason="El tipo debe ser movie o series")
    season = str(values.get("season") or "").strip()
    episode = str(values.get("episode") or "").strip()
    try:
        season_n = int(season) if season else None
        episode_n = int(episode) if episode else None
    except ValueError:
        return Entry(raw=raw, reason="Temporada o episodio inválidos")
    if episode_n and not season_n:
        return Entry(raw=raw, reason="Un episodio necesita temporada")
    return Entry(
        raw=raw,
        kind=kind,
        tmdb=int(tmdb),
        season=season_n,
        episode=episode_n,
    )


def _guard_size(text: str) -> None:
    if len(_lines(text)) > MAX_LINES:
        raise IntakeError(
            f"Máximo {MAX_LINES} líneas por importación; divide la lista"
        )


def parse_entries(text: str, *, kind: str = DEFAULT_KIND) -> list[Entry]:
    """Convierte texto pegado en entradas estructuradas.

    Acepta URLs de TMDB, claves estables, IDs sueltos y CSV con cabecera
    `tmdb,kind,season,episode`. No toca red ni base de datos.
    """
    default_kind = "series" if kind == "series" else "movie"
    lines = _lines(text)
    if not lines:
        return []

    entries: list[Entry] = []
    if _csv_mode(lines):
        rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
        for row in rows:
            raw = ",".join(str(value or "") for value in row.values()).strip(",")
            entries.append(
                _entry_from_values(row, default_kind=default_kind, raw=raw)
            )
        return entries

    for line in lines:
        if line.startswith("#"):
            continue
        entries.append(_parse_line(line, default_kind=default_kind))
    return entries


def _parse_line(line: str, *, default_kind: str) -> Entry:
    raw = line.strip()
    if not raw:
        return Entry(raw=raw, reason="Línea vacía")

    match = _TMDB_LIST.search(raw)
    if match:
        return Entry(raw=raw, kind="list", list_id=int(match.group(1)))

    match = _TMDB_MOVIE.search(raw)
    if match:
        return Entry(raw=raw, kind="movie", tmdb=int(match.group(1)))

    match = _TMDB_TV.search(raw)
    if match:
        season = int(match.group(2)) if match.group(2) else None
        episode = int(match.group(3)) if match.group(3) else None
        if episode and not season:
            return Entry(raw=raw, reason="Un episodio necesita temporada")
        return Entry(
            raw=raw,
            kind="series",
            tmdb=int(match.group(1)),
            season=season,
            episode=episode,
        )

    match = _KEY_MOVIE.match(raw)
    if match:
        scheme, value = match.group(1).lower(), match.group(2)
        if scheme != "tmdb" or not value.isdigit():
            return Entry(raw=raw, reason="Solo se admiten claves movie:tmdb:<id>")
        return Entry(raw=raw, kind="movie", tmdb=int(value))

    match = _KEY_SERIES.match(raw)
    if match:
        scheme, value = match.group(1).lower(), match.group(2)
        if scheme != "tmdb" or not value.isdigit():
            return Entry(raw=raw, reason="Solo se admiten claves series:tmdb:<id>")
        season = int(match.group(3)) if match.group(3) else None
        episode = int(match.group(4)) if match.group(4) else None
        if episode and not season:
            return Entry(raw=raw, reason="Un episodio necesita temporada")
        return Entry(
            raw=raw,
            kind="series",
            tmdb=int(value),
            season=season,
            episode=episode,
        )

    if _BARE_ID.match(raw):
        return Entry(
            raw=raw,
            kind=default_kind,
            tmdb=int(raw),
        )

    if raw.lower().startswith("magnet:"):
        return Entry(
            raw=raw,
            reason="Para un magnet, descarga el .torrent y usa «Subir .torrent»",
        )
    if raw.lower().startswith(("http://", "https://")):
        return Entry(
            raw=raw,
            reason="No se reconoce este enlace. Usa una URL de película, serie o lista de TMDB",
        )
    return Entry(raw=raw, reason="No se reconoce como ID, clave o URL de TMDB")


def _dedupe(entries: Iterable[Entry]) -> list[Entry]:
    seen: set[tuple[str, int, int, int]] = set()
    output: list[Entry] = []
    for entry in entries:
        if entry.kind == "unknown" or not entry.tmdb:
            output.append(entry)
            continue
        if entry.identity in seen:
            output.append(
                Entry(
                    raw=entry.raw,
                    kind=entry.kind,
                    tmdb=entry.tmdb,
                    season=entry.season,
                    episode=entry.episode,
                    reason="Repetido en la lista",
                    repeated=True,
                )
            )
            continue
        seen.add(entry.identity)
        output.append(entry)
    return output


def _expand_entries(
    client: TmdbClient, entries: list[Entry]
) -> tuple[list[Entry], list[str]]:
    """Convierte una URL de lista de TMDB en sus títulos.

    Las listas pueden mezclar películas y series. Una serie entra por
    temporadas; si no se puede leer su ficha, queda como serie sin rango.
    """
    expanded: list[Entry] = []
    names: list[str] = []
    truncated = False

    for entry in entries:
        if entry.kind != "list" or not entry.list_id:
            expanded.append(entry)
            continue
        try:
            first = client.list_details(entry.list_id, page=1)
        except Exception as exc:
            expanded.append(
                Entry(
                    raw=entry.raw,
                    reason=f"No se pudo leer la lista: {str(exc)[:180]}",
                )
            )
            continue

        name = str(first.get("name") or f"Lista {entry.list_id}").strip()
        names.append(name)
        items = list(first.get("items") or [])
        total_pages = max(1, int(first.get("total_pages") or 1))
        for page in range(2, total_pages + 1):
            if len(expanded) >= MAX_LINES:
                truncated = True
                break
            try:
                payload = client.list_details(entry.list_id, page=page)
            except Exception:
                break
            items.extend(payload.get("items") or [])

        for item in items:
            if len(expanded) >= MAX_LINES:
                truncated = True
                break
            try:
                tmdb = int(item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if not tmdb:
                continue
            media_type = str(
                item.get("media_type")
                or ("tv" if item.get("name") else "movie")
            ).lower()
            if media_type == "tv":
                try:
                    series = client.tv_details(tmdb)
                except Exception:
                    series = {}
                seasons = sorted({
                    int(row.get("season_number") or 0)
                    for row in (series.get("seasons") or [])
                    if int(row.get("season_number") or 0) > 0
                })
                if not seasons:
                    expanded.append(Entry(
                        raw=f"https://www.themoviedb.org/tv/{tmdb}",
                        kind="series",
                        tmdb=tmdb,
                    ))
                    continue
                for season in seasons:
                    if len(expanded) >= MAX_LINES:
                        truncated = True
                        break
                    expanded.append(Entry(
                        raw=(
                            f"https://www.themoviedb.org/tv/{tmdb}"
                            f"/season/{season}"
                        ),
                        kind="series",
                        tmdb=tmdb,
                        season=season,
                    ))
            else:
                expanded.append(Entry(
                    raw=f"https://www.themoviedb.org/movie/{tmdb}",
                    kind="movie",
                    tmdb=tmdb,
                ))
        if truncated:
            break

    if truncated:
        expanded.append(Entry(
            raw="…",
            reason=f"Lista truncada: máximo {MAX_LINES} títulos por alta",
        ))
    return expanded, names


def _cache_key(config: Config, entry: Entry) -> tuple[str, str, int, int, int]:
    return (
        str(config.db_path),
        entry.kind,
        int(entry.tmdb or 0),
        int(entry.season or 0),
        int(entry.episode or 0),
    )


def _cache_get(config: Config, entry: Entry) -> dict[str, Any] | None:
    key = _cache_key(config, entry)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is None or now - cached[0] > CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return dict(cached[1])


def _cache_put(config: Config, entry: Entry, data: dict[str, Any]) -> None:
    with _LOCK:
        _CACHE[_cache_key(config, entry)] = (time.monotonic(), dict(data))
        if len(_CACHE) > 2000:
            cutoff = time.monotonic() - CACHE_TTL
            for key, value in list(_CACHE.items()):
                if value[0] < cutoff:
                    _CACHE.pop(key, None)


def _series_meta(client: TmdbClient, tmdb: int) -> dict[str, Any]:
    raw = client.tv_details(int(tmdb))
    item = tv_item(raw)
    if item is None:
        raise IntakeError("TMDB no devolvió una serie válida")
    return item


def _season_payload(client: TmdbClient, tmdb: int, season: int) -> dict[str, Any]:
    return client.season_details(int(tmdb), int(season))


def _resolve(
    client: TmdbClient,
    entry: Entry,
    *,
    movie_cache: dict[int, Any],
    series_cache: dict[int, Any],
    season_cache: dict[tuple[int, int], Any],
) -> dict[str, Any]:
    if entry.kind == "unknown" or not entry.tmdb:
        return {
            "state": "unknown",
            "reason": entry.reason or "Entrada no reconocida",
        }
    if entry.repeated:
        return {"state": "duplicate", "reason": entry.reason}

    if entry.kind == "series" and not entry.season:
        try:
            series = series_cache.get(entry.tmdb)
            if series is None:
                series = _series_meta(client, entry.tmdb)
                series_cache[entry.tmdb] = series
        except Exception as exc:
            return {"state": "error", "reason": str(exc)[:200]}
        return {
            "state": "needs_range",
            "reason": "Elige una temporada o un episodio para importar",
            "title": series.get("title"),
            "year": series.get("year"),
            "poster_path": series.get("poster_path"),
            "episodes": sum(
                int(item.get("episode_count") or 0)
                for item in (series.get("seasons") or [])
                if isinstance(item, dict)
            )
            or None,
            "href": f"#/series/{entry.tmdb}",
        }

    if entry.kind == "movie":
        try:
            raw = movie_cache.get(entry.tmdb)
            if raw is None:
                raw = client.movie_details(int(entry.tmdb))
                movie_cache[entry.tmdb] = raw
            item = movie_item(raw)
        except Exception as exc:
            return {"state": "error", "reason": str(exc)[:200]}
        if item is None:
            return {"state": "unknown", "reason": "TMDB no devolvió una película"}
        return {
            "state": "ready",
            "title": item.get("title"),
            "year": item.get("year"),
            "poster_path": item.get("poster_path"),
        }

    try:
        series = series_cache.get(entry.tmdb)
        if series is None:
            series = _series_meta(client, entry.tmdb)
            series_cache[entry.tmdb] = series
        payload = season_cache.get((entry.tmdb, int(entry.season or 0)))
        if payload is None:
            payload = _season_payload(
                client, entry.tmdb, int(entry.season or 0)
            )
            season_cache[(entry.tmdb, int(entry.season or 0))] = payload
        episodes = episode_items(int(entry.season or 0), payload)
    except Exception as exc:
        return {"state": "error", "reason": str(exc)[:200]}

    if entry.episode:
        found = next(
            (item for item in episodes if int(item.get("episode") or 0) == entry.episode),
            None,
        )
        if found is None:
            return {
                "state": "unknown",
                "reason": f"TMDB no tiene el episodio {entry.season}x{entry.episode}",
                "title": series.get("title"),
                "year": series.get("year"),
                "poster_path": series.get("poster_path"),
            }
        return {
            "state": "ready",
            "title": series.get("title"),
            "year": series.get("year"),
            "poster_path": series.get("poster_path"),
            "episode_title": found.get("name"),
            "episodes": 1,
        }
    if not episodes:
        return {
            "state": "unknown",
            "reason": f"TMDB no tiene episodios en la temporada {entry.season}",
            "title": series.get("title"),
            "year": series.get("year"),
            "poster_path": series.get("poster_path"),
        }
    return {
        "state": "ready",
        "title": series.get("title"),
        "year": series.get("year"),
        "poster_path": series.get("poster_path"),
        "episodes": len(episodes),
    }


def _existing(db_path, entry: Entry) -> dict[str, Any] | None:
    key = entry.key
    if not key or entry.kind == "series" and not entry.episode and entry.season:
        return None
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id, title, year, poster_path, origin FROM media_items "
            "WHERE key = ?",
            (key,),
        ).fetchone()
    return dict(row) if row is not None else None


def _season_existing(db_path, tmdb: int, season: int) -> int:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM media_items "
            "WHERE kind = 'series' AND tmdb = ? AND season = ?",
            (int(tmdb), int(season)),
        ).fetchone()
    return int(row[0] or 0) if row is not None else 0


def preflight(
    config: Config,
    text: str,
    *,
    kind: str = DEFAULT_KIND,
    client: TmdbClient | None = None,
) -> dict[str, Any]:
    """Analiza una lista sin escribir nada en el catálogo."""
    _guard_size(text)
    client = client or _client(config)
    entries, _names = _expand_entries(
        client, parse_entries(text, kind=kind)
    )
    entries = _dedupe(entries)
    if not entries:
        raise IntakeError("No hay nada que importar")
    movie_cache: dict[int, Any] = {}
    series_cache: dict[int, Any] = {}
    season_cache: dict[tuple[int, int], Any] = {}

    rows: list[dict[str, Any]] = []
    for entry in entries:
        row: dict[str, Any] = {
            "raw": entry.raw,
            "kind": entry.kind,
            "tmdb": entry.tmdb,
            "season": entry.season,
            "episode": entry.episode,
            "key": entry.key,
            "state": "unknown",
            "reason": None,
            "title": None,
            "year": None,
            "poster_path": None,
            "episodes": None,
            "media_id": None,
            "href": None,
        }
        if entry.kind == "unknown" or not entry.tmdb:
            row.update(
                state="unknown",
                reason=entry.reason or "Entrada no reconocida",
            )
            rows.append(row)
            continue
        existing = _existing(config.db_path, entry)
        if existing is not None:
            row.update(
                state="duplicate",
                reason="Ya está en el catálogo",
                title=existing.get("title"),
                year=existing.get("year"),
                poster_path=existing.get("poster_path"),
                media_id=existing.get("id"),
                href=f"#/title/{existing.get('id')}",
            )
            rows.append(row)
            continue
        cached = _cache_get(config, entry)
        resolved = cached or _resolve(
            client,
            entry,
            movie_cache=movie_cache,
            series_cache=series_cache,
            season_cache=season_cache,
        )
        if cached is None and resolved.get("state") in ("ready", "needs_range"):
            _cache_put(config, entry, resolved)
        if (
            resolved.get("state") == "ready"
            and entry.kind == "series"
            and entry.season
            and not entry.episode
        ):
            have = _season_existing(config.db_path, entry.tmdb, entry.season)
            total = int(resolved.get("episodes") or 0)
            if total and have >= total:
                resolved = {
                    **resolved,
                    "state": "duplicate",
                    "reason": "La temporada ya está en el catálogo",
                    "href": f"#/series/{entry.tmdb}",
                }
            elif have:
                resolved = {
                    **resolved,
                    "reason": f"Completa {total - have} episodios de la temporada",
                }
        row.update(resolved)
        if row.get("state") == "needs_range" and not row.get("href") and entry.tmdb:
            row["href"] = f"#/series/{entry.tmdb}"
        rows.append(row)

    summary = {
        "total": len(rows),
        "ready": sum(1 for row in rows if row["state"] == "ready"),
        "duplicate": sum(1 for row in rows if row["state"] == "duplicate"),
        "needs_range": sum(1 for row in rows if row["state"] == "needs_range"),
        "unknown": sum(1 for row in rows if row["state"] == "unknown"),
        "error": sum(1 for row in rows if row["state"] == "error"),
        "max_lines": MAX_LINES,
    }
    return {"summary": summary, "items": rows}


def _media_id(db_path, key: str) -> int | None:
    if not key:
        return None
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id FROM media_items WHERE key = ?", (key,)
        ).fetchone()
    return int(row["id"]) if row is not None else None


def _mark_origin(db_path, keys: Iterable[str], *, batch_id: int) -> None:
    clean = [key for key in keys if key]
    if not clean:
        return
    stamp = _now()
    detail = f"Lote #{batch_id}"
    with closing(connect(db_path)) as conn:
        conn.executemany(
            """
            UPDATE media_items SET
                origin = COALESCE(origin, 'import'),
                origin_detail = COALESCE(origin_detail, ?),
                origin_at = COALESCE(origin_at, ?)
            WHERE key = ?
            """,
            [(detail, stamp, key) for key in clean],
        )
        conn.commit()


def set_follow(db_path, keys: Iterable[str], follow: bool) -> int:
    """Marca o desmarca el seguimiento de las claves que no están en un *arr."""
    clean = [str(key) for key in keys if key]
    if not clean:
        return 0
    changed = 0
    with closing(connect(db_path)) as conn:
        for start in range(0, len(clean), 500):
            chunk = clean[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            if follow:
                cursor = conn.execute(
                    f"""
                    UPDATE media_items SET
                        monitored = 1,
                        monitored_at = COALESCE(
                            monitored_at,
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        ),
                        monitor_source = COALESCE(monitor_source, 'import'),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE key IN ({placeholders}) AND library_added = 0
                    """,
                    chunk,
                )
            else:
                cursor = conn.execute(
                    f"""
                    UPDATE media_items SET
                        monitored = 0,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE key IN ({placeholders}) AND library_added = 0
                    """,
                    chunk,
                )
            changed += int(cursor.rowcount or 0)
        conn.commit()
    return changed


def _add_entry(
    config: Config,
    client: TmdbClient,
    entry: Entry,
    *,
    series_cache: dict[int, Any],
    season_cache: dict[tuple[int, int], Any],
) -> dict[str, Any]:
    if entry.kind == "movie" and entry.tmdb:
        result = admin.add_movie(config, entry.tmdb, origin="import")
        key = result.get("key")
        return {
            "key": key,
            "media_id": result.get("asset_id"),
            "detail": None,
            "keys": [key],
            "title": _title_for(config.db_path, key),
            "year": _year_for(config.db_path, key),
            "inserted": int(result.get("inserted") or 0),
            "updated": int(result.get("updated") or 0),
        }
    if entry.kind == "series" and entry.tmdb and entry.season:
        tmdb = int(entry.tmdb)
        season = int(entry.season)
        series = series_cache.get(tmdb)
        if series is None:
            series = _series_meta(client, tmdb)
            series_cache[tmdb] = series
        payload = season_cache.get((tmdb, season))
        if payload is None:
            payload = _season_payload(client, tmdb, season)
            season_cache[(tmdb, season)] = payload
        episodes = episode_items(season, payload)
        if entry.episode:
            episodes = [
                item
                for item in episodes
                if int(item.get("episode") or 0) == int(entry.episode)
            ]
            if not episodes:
                raise IntakeError(
                    f"TMDB no tiene el episodio {season}x{entry.episode}"
                )
        if not episodes:
            raise IntakeError(f"TMDB no tiene episodios en la temporada {season}")
        report = upsert_episodes(
            config.db_path,
            series,
            episodes,
            source="tmdb",
            origin="import",
        )
        keys = [
            f"series:tmdb:{tmdb}:{season}:{int(item.get('episode') or 0)}"
            for item in episodes
        ]
        first = keys[0] if keys else None
        return {
            "key": first if entry.episode else None,
            "media_id": _media_id(config.db_path, first) if first else None,
            "detail": (
                None
                if entry.episode
                else f"Temporada {season} · {len(episodes)} episodios"
            ),
            "keys": keys,
            "title": series.get("title"),
            "year": series.get("year"),
            "inserted": int(report.get("inserted") or 0),
            "updated": int(report.get("updated") or 0),
        }
    raise IntakeError("Entrada sin destino de importación")


def confirm(
    config: Config,
    text: str,
    *,
    kind: str = DEFAULT_KIND,
    label: str | None = None,
    requested_by: str | None = None,
    follow: bool = True,
    client: TmdbClient | None = None,
) -> dict[str, Any]:
    """Importa la lista y deja un lote auditable en la recepción."""
    _guard_size(text)
    client = client or _client(config)
    entries, list_names = _expand_entries(
        client, parse_entries(text, kind=kind)
    )
    entries = _dedupe(entries)
    if not entries:
        raise IntakeError("No hay nada que importar")
    default_label = (
        f"Lista de TMDB · {list_names[0]}"
        if list_names
        else "Importación de lista"
    )
    batch_id = _create_batch(
        config.db_path,
        source="import",
        label=(label or "").strip() or default_label,
        requested_by=requested_by,
        total=len(entries),
    )
    try:
        touched = _run_batch(config, client, entries, batch_id=batch_id)
    except Exception:
        _finish_batch(
            config.db_path,
            batch_id,
            added=0,
            duplicate=0,
            needs_range=0,
            unknown=0,
            errors=len(entries),
        )
        raise
    set_follow(config.db_path, touched or [], follow)
    return batch_detail(config.db_path, batch_id) or {"id": batch_id}


def add_torrent(
    config: Config,
    *,
    filename: str,
    raw: bytes,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Envía un .torrent manual a qBittorrent y lo deja en la recepción.

    Es un atajo explícito del usuario: no pasa por admisión ni crea un
    compromiso de seed. qBittorrent manda sobre ese torrent.
    """
    from .qbit_write import QbittorrentWriteClient
    from .torrent_meta import info_dict_bytes, torrent_name

    if not raw or not raw.startswith(b"d"):
        raise IntakeError("El archivo no parece un .torrent")
    if len(raw) > MAX_TORRENT_BYTES:
        raise IntakeError("El .torrent es demasiado grande")
    info = info_dict_bytes(raw)
    if not info:
        raise IntakeError("No se pudo leer el .torrent")
    info_hash = hashlib.sha1(info).hexdigest()
    name = torrent_name(raw) or Path(filename or "").stem or "Torrent manual"
    save_path = str(config.ondemand_save_path)
    try:
        Path(save_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        client = QbittorrentWriteClient.from_config(config)
        client.add_torrent(
            raw,
            save_path=save_path,
            category="ondemand",
            paused=False,
            sequential=True,
            first_last=True,
            tags="manual",
            filename=filename or f"{name}.torrent",
        )
    except Exception as exc:
        raise IntakeError(
            f"No se pudo añadir a qBittorrent: {str(exc)[:200]}"
        ) from exc

    batch_id = _create_batch(
        config.db_path,
        source="torrent",
        label=f"Torrent manual · {name}",
        requested_by=requested_by,
        total=1,
    )
    _record_item(
        config.db_path,
        batch_id=batch_id,
        entry=Entry(raw=filename or f"{name}.torrent", kind="torrent"),
        state="added",
        detail=f"Añadido a qBittorrent · {info_hash[:12]}",
        media_id=None,
        key=None,
        title=name,
        year=None,
    )
    _finish_batch(
        config.db_path,
        batch_id,
        added=1,
        duplicate=0,
        needs_range=0,
        unknown=0,
        errors=0,
    )
    return {
        "batch_id": batch_id,
        "name": name,
        "info_hash": info_hash,
        "category": "ondemand",
        "save_path": save_path,
    }


def _create_batch(
    db_path,
    *,
    source: str,
    label: str,
    requested_by: str | None,
    total: int,
) -> int:
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO intake_batches(source, label, state, requested_by, total)
            VALUES(?, ?, 'running', ?, ?)
            """,
            (source, label[:200], requested_by, int(total)),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _run_batch(
    config: Config,
    client: TmdbClient,
    entries: list[Entry],
    *,
    batch_id: int,
) -> list[str]:
    movie_cache: dict[int, Any] = {}
    series_cache: dict[int, Any] = {}
    season_cache: dict[tuple[int, int], Any] = {}
    added = duplicate = needs_range = unknown = errors = 0
    touched_keys: list[str] = []

    for entry in entries:
        state = "unknown"
        detail = entry.reason
        media_id = None
        key = entry.key
        title = None
        year = None
        try:
            existing = _existing(config.db_path, entry)
            if existing is not None:
                state = "duplicate"
                detail = entry.reason or "Ya está en el catálogo"
                media_id = existing.get("id")
                title = existing.get("title")
                year = existing.get("year")
            elif entry.repeated:
                state = "duplicate"
                detail = entry.reason or "Repetido en la lista"
            elif entry.kind == "unknown" or not entry.tmdb:
                state = "unknown"
                detail = entry.reason or "Entrada no reconocida"
            elif entry.kind == "series" and not entry.season:
                state = "needs_range"
                detail = "Elige una temporada o un episodio"
                meta = series_cache.get(entry.tmdb)
                if meta is None:
                    meta = _series_meta(client, entry.tmdb)
                    series_cache[entry.tmdb] = meta
                title = meta.get("title")
                year = meta.get("year")
            else:
                result = _add_entry(
                    config,
                    client,
                    entry,
                    series_cache=series_cache,
                    season_cache=season_cache,
                )
                state = "added"
                detail = result.get("detail")
                media_id = result.get("media_id")
                key = result.get("key") or entry.key
                title = result.get("title")
                year = result.get("year")
                touched_keys.extend(result.get("keys") or [])
                if (
                    int(result.get("inserted") or 0) == 0
                    and int(result.get("updated") or 0) > 0
                ):
                    state = "duplicate"
                    detail = "Ya estaba en el catálogo"
        except Exception as exc:
            state = "error"
            detail = str(exc)[:240]
        if state == "added" and not detail:
            detail = "En el catálogo"

        _record_item(
            config.db_path,
            batch_id=batch_id,
            entry=entry,
            state=state,
            detail=detail,
            media_id=media_id,
            key=key,
            title=title,
            year=year,
        )
        if state == "added":
            added += 1
        elif state == "duplicate":
            duplicate += 1
        elif state == "needs_range":
            needs_range += 1
        elif state == "unknown":
            unknown += 1
        else:
            errors += 1

    _mark_origin(config.db_path, touched_keys, batch_id=batch_id)
    _finish_batch(
        config.db_path,
        batch_id,
        added=added,
        duplicate=duplicate,
        needs_range=needs_range,
        unknown=unknown,
        errors=errors,
    )
    return touched_keys


def _title_for(db_path, key: str | None) -> str | None:
    if not key:
        return None
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT title FROM media_items WHERE key = ?", (key,)
        ).fetchone()
    return row["title"] if row is not None else None


def _year_for(db_path, key: str | None) -> int | None:
    if not key:
        return None
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT year FROM media_items WHERE key = ?", (key,)
        ).fetchone()
    return int(row["year"]) if row is not None and row["year"] else None


def _record_item(
    db_path,
    *,
    batch_id: int,
    entry: Entry,
    state: str,
    detail: str | None,
    media_id: int | None,
    key: str | None,
    title: str | None,
    year: int | None,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO intake_items(
                batch_id, raw, key, kind, tmdb, season, episode,
                title, year, state, detail, media_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(batch_id),
                entry.raw[:500],
                key,
                entry.kind,
                entry.tmdb,
                entry.season,
                entry.episode,
                title,
                year,
                state,
                (detail or "")[:500] or None,
                media_id,
            ),
        )
        conn.commit()


def _finish_batch(
    db_path,
    batch_id: int,
    *,
    added: int,
    duplicate: int,
    needs_range: int,
    unknown: int,
    errors: int,
) -> None:
    state = "error" if added == 0 and errors else "partial" if errors else "done"
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE intake_batches SET
                state = ?, added = ?, duplicate = ?, needs_range = ?,
                unknown = ?, errors = ?,
                finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                state,
                int(added),
                int(duplicate),
                int(needs_range),
                int(unknown),
                int(errors),
                int(batch_id),
            ),
        )
        conn.commit()


def _batch_row(row) -> dict[str, Any]:
    item = dict(row)
    item["origin"] = item.get("source")
    return item


def reception(
    db_path, *, limit: int = 8, include_flows: bool = True
) -> dict[str, Any]:
    """Lo que ha entrado y, si se pide, lo que corre solo."""
    limit = max(1, min(int(limit), 40))
    with closing(connect(db_path)) as conn:
        batches = [
            _batch_row(row)
            for row in conn.execute(
                "SELECT * FROM intake_batches ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        today = conn.execute(
            """
            SELECT COUNT(*) AS batches,
                   COALESCE(SUM(added), 0) AS added,
                   COALESCE(SUM(duplicate), 0) AS duplicate,
                   COALESCE(SUM(needs_range), 0) AS needs_range,
                   COALESCE(SUM(unknown), 0) AS unknown,
                   COALESCE(SUM(errors), 0) AS errors
            FROM intake_batches
            WHERE substr(created_at, 1, 10) = date('now')
            """
        ).fetchone()
    flows = _flows(db_path) if include_flows else []
    return {
        "batches": batches,
        "today": dict(today) if today is not None else {},
        "flows": flows,
        "limits": {"max_lines": MAX_LINES},
    }


def _flow_summary(kind: str, result: dict[str, Any] | None) -> str:
    data = result or {}
    if kind == "arr-mirror":
        mirror = data.get("mirror") or {}
        radarr = mirror.get("radarr") or {}
        sonarr = mirror.get("sonarr") or {}
        items = int(radarr.get("items") or 0) + int(sonarr.get("episodes") or 0)
        written = int(data.get("written") or 0)
        return f"{items} títulos espejados · {written} .strm"
    if kind == "rss-sync":
        return (
            f"{int(data.get('new_titles') or 0)} títulos nuevos · "
            f"{int(data.get('inserted') or 0)} releases"
        )
    if kind == "backfill":
        return (
            f"{int(data.get('checked') or 0)} vistos · "
            f"{int(data.get('found') or 0)} encontrados"
        )
    if kind == "bridge":
        bridged = data.get("bridged") or []
        return f"{len(bridged)} copiados a Radarr/Sonarr"
    if kind == "monitor":
        return (
            f"{int(data.get('found') or 0)} con fuente · "
            f"{int(data.get('checked') or 0)} revisados"
        )
    return ""


_FLOW_LABELS = {
    "arr-mirror": "Radarr y Sonarr",
    "rss-sync": "Novedades RSS",
    "backfill": "Fondo antiguo",
    "bridge": "Puente a Radarr/Sonarr",
    "monitor": "Seguimiento",
}


def _flows(db_path) -> list[dict[str, Any]]:
    recent = jobs.list_jobs(db_path, limit=200)
    latest: dict[str, dict[str, Any]] = {}
    for job in recent:
        kind = job.get("kind")
        if kind in _FLOW_LABELS and kind not in latest:
            latest[kind] = job
    output: list[dict[str, Any]] = []
    for kind, label in _FLOW_LABELS.items():
        job = latest.get(kind)
        if job is None:
            output.append(
                {
                    "kind": kind,
                    "label": label,
                    "state": None,
                    "finished_at": None,
                    "summary": "Sin ejecutar",
                }
            )
            continue
        output.append(
            {
                "kind": kind,
                "label": label,
                "state": job.get("state"),
                "finished_at": job.get("finished_at") or job.get("created_at"),
                "summary": _flow_summary(kind, job.get("result")),
                "error": job.get("error"),
            }
        )
    return output


def batch_detail(db_path, batch_id: int) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        batch = conn.execute(
            "SELECT * FROM intake_batches WHERE id = ?", (int(batch_id),)
        ).fetchone()
        if batch is None:
            return None
        items = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.*, m.poster_path
                FROM intake_items i
                LEFT JOIN media_items m ON m.id = i.media_id
                WHERE i.batch_id = ?
                ORDER BY i.id
                """,
                (int(batch_id),),
            ).fetchall()
        ]
    data = _batch_row(batch)
    data["items"] = items
    return data


def origins(db_path, *, limit: int = 20) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT origin, COUNT(*) AS total
            FROM media_items
            GROUP BY origin
            ORDER BY total DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]
