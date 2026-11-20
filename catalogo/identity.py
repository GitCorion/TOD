"""Claves estables.

La URL pública nunca lleva un id autoincrement. Lleva una clave:

    movie:tmdb:9737
    movie:imdb:tt0112442
    movie:legacy:41              (sin tmdb; pendiente de identidad)
    series:tmdb:2190:29:4
    series:legacy:41:29:4

El puntero al torrent actual vive en play_targets y puede cambiar sin tocar
el .strm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MOVIE_NEW = re.compile(r"^movie:(tmdb|imdb|legacy):([A-Za-z0-9]+)$")
SERIES_NEW = re.compile(
    r"^series:(tmdb|legacy):([A-Za-z0-9]+):(\d{1,4}):(\d{1,5})$"
)
MOVIE_OLD = re.compile(r"^movie:(\d+)$")
SERIES_OLD = re.compile(r"^series:(\d+):(\d{1,4}):(\d{1,5})$")


@dataclass(frozen=True)
class AssetKey:
    kind: str
    scheme: str
    value: str
    season: int | None = None
    episode: int | None = None

    def __str__(self) -> str:
        if self.kind == "movie":
            return f"movie:{self.scheme}:{self.value}"
        return (
            f"series:{self.scheme}:{self.value}:"
            f"{self.season or 0}:{self.episode or 0}"
        )


def parse_key(key: str) -> AssetKey | None:
    text = (key or "").strip()
    match = MOVIE_NEW.match(text)
    if match:
        return AssetKey("movie", match.group(1), match.group(2))
    match = SERIES_NEW.match(text)
    if match:
        return AssetKey(
            "series",
            match.group(1),
            match.group(2),
            int(match.group(3)),
            int(match.group(4)),
        )
    return None


def normalize_key(key: str) -> str | None:
    """Acepta claves nuevas y las antiguas de assets.db.

    Devuelve None si no hay una identidad fiable (p. ej. movie:0 sin id legacy).
    """
    parsed = parse_key(key)
    if parsed:
        return str(parsed)

    match = MOVIE_OLD.match((key or "").strip())
    if match:
        tmdb = int(match.group(1))
        return f"movie:tmdb:{tmdb}" if tmdb > 0 else None

    match = SERIES_OLD.match((key or "").strip())
    if match:
        tmdb = int(match.group(1))
        if tmdb <= 0:
            return None
        return f"series:tmdb:{tmdb}:{int(match.group(2))}:{int(match.group(3))}"
    return None


def asset_key(
    kind: str,
    tmdb: int | None,
    season: int | None = None,
    episode: int | None = None,
    legacy_id: int | None = None,
) -> tuple[str, str]:
    """Devuelve (clave, identity_status)."""
    if kind == "movie":
        if tmdb and int(tmdb) > 0:
            return f"movie:tmdb:{int(tmdb)}", "ok"
        if legacy_id is None:
            raise ValueError("película sin tmdb y sin legacy_id")
        return f"movie:legacy:{int(legacy_id)}", "needs_identity"

    if kind == "series":
        if tmdb and int(tmdb) > 0:
            return (
                f"series:tmdb:{int(tmdb)}:{int(season or 0)}:{int(episode or 0)}",
                "ok",
            )
        if legacy_id is None:
            raise ValueError("serie sin tmdb y sin legacy_id")
        return (
            f"series:legacy:{int(legacy_id)}:{int(season or 0)}:{int(episode or 0)}",
            "needs_identity",
        )

    raise ValueError(f"tipo desconocido: {kind}")


def play_url(key: str, base: str = "") -> str:
    """URL que irá dentro del .strm. No permite arrancar descargas."""
    from urllib.parse import quote

    return f"{base}/play?key={quote(key, safe='')}&download=0"
