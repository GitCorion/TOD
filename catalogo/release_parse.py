"""Parseo de nombres de release de series al estilo Sonarr.

Sonarr separa temporada y episodios del nombre antes de decidir si un
release sirve para el episodio pedido. Aquí aplicamos las mismas reglas con
una implementación propia (Sonarr es GPLv3 y TOD es MIT).

Casos cubiertos:

- ``S01E02``, ``S01.E02``, ``S01-E02``, ``S01E02E03``, ``S01E02-E03``.
- ``1x02``.
- ``Temporada 1 Episodio 2``, ``Season 1 Episode 2``, ``T1E2``.
- ``S01``, ``Temporada 1``, ``1ª Temporada``, ``2ºTemporada``.
- ``[Primera Temporada]``, ``[Segunda Temporada]``...
- ``T9``/``Temp 9``.
- Packs de serie completa, miniseries y packs sin temporada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORDINALS = {
    "primera": 1,
    "segunda": 2,
    "tercera": 3,
    "cuarta": 4,
    "quinta": 5,
    "sexta": 6,
    "septima": 7,
    "séptima": 7,
    "octava": 8,
    "novena": 9,
    "decima": 10,
    "décima": 10,
    "undecima": 11,
    "undécima": 11,
    "duodecima": 12,
    "duodécima": 12,
}

# SxxEyy, con continuación opcional del tipo E02, -E02 o -02.
_SXXEYY = re.compile(
    r"(?i)(?<![a-z])s(\d{1,2})[\s._-]*e(\d{1,3})"
    r"(?:(?:[\s._-]*e|-e?)(\d{1,3}))?"
)
# 1x02, 01x02.
_XXYY = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)")
# Temporada 1 Episodio 2 / Season 1 Episode 2 / T1E2.
_WORD_EPISODE = re.compile(
    r"(?i)\b(?:temporada|season)\s*(\d{1,2})[\s._-]*"
    r"(?:episodio|episode|cap[ií]tulo|cap)\s*(\d{1,3})\b"
)
_T_E = re.compile(r"(?i)\bt(\d{1,2})[\s._-]*e(\d{1,3})\b")

_SEASON_S = re.compile(r"(?i)(?<![a-z])s(\d{1,2})(?!\d)")
_SEASON_WORD = re.compile(r"(?i)\b(?:temporada|season|serie)\s*(\d{1,2})\b")
_SEASON_NUMBERED = re.compile(r"(?i)\b(\d{1,2})\s*[ªº°]?\s*temporada\b")
_SEASON_ORDINAL = re.compile(
    r"(?i)\b(" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r")\s*temporada\b"
)
_SEASON_T = re.compile(r"(?i)\b(?:t|temp)\s*(\d{1,2})\b")

_COMPLETE = re.compile(
    r"(?i)\b(?:complete|completa|completo)\s*(?:series|serie|colecci[oó]n|pack)\b"
    r"|\bserie\s+completa\b"
    r"|\bpack\s+completo\b"
    r"|\bmini-?serie\b"
)

PACK = re.compile(r"(?i)\b(?:pack|complete|completa|completo|temporada|season|mini-?serie)\b")


@dataclass(frozen=True)
class ParsedRelease:
    """Temporada y episodios detectados en el nombre de un release."""

    season: int | None
    episodes: tuple[int, ...]
    complete_series: bool = False

    @property
    def is_pack(self) -> bool:
        return not self.episodes


def _episode_range(start: int, end: int | None) -> tuple[int, ...]:
    if end is None or end <= start:
        return (start,)
    # Un rango absurdo sería ruido del título, no una lista de episodios.
    if end - start > 50:
        return (start,)
    return tuple(range(start, end + 1))


def _season_of_text(text: str) -> int | None:
    match = _SEASON_S.search(text) or _SEASON_WORD.search(text)
    if match:
        return int(match.group(1))
    match = _SEASON_NUMBERED.search(text)
    if match:
        return int(match.group(1))
    match = _SEASON_ORDINAL.search(text)
    if match:
        return _ORDINALS[match.group(1).casefold()]
    match = _SEASON_T.search(text)
    if match:
        return int(match.group(1))
    return None


def parse_release(title: str | None) -> ParsedRelease:
    """Devuelve temporada, episodios y si es pack de serie completa."""
    text = title or ""

    match = _SXXEYY.search(text)
    if match:
        season = int(match.group(1))
        episodes = _episode_range(int(match.group(2)), (
            int(match.group(3)) if match.group(3) else None
        ))
        return ParsedRelease(season=season, episodes=episodes)

    match = _XXYY.search(text)
    if match:
        return ParsedRelease(
            season=int(match.group(1)),
            episodes=(int(match.group(2)),),
        )

    match = _WORD_EPISODE.search(text)
    if match:
        return ParsedRelease(
            season=int(match.group(1)),
            episodes=(int(match.group(2)),),
        )

    match = _T_E.search(text)
    if match:
        return ParsedRelease(
            season=int(match.group(1)),
            episodes=(int(match.group(2)),),
        )

    return ParsedRelease(
        season=_season_of_text(text),
        episodes=(),
        complete_series=bool(_COMPLETE.search(text)),
    )


def parse_episode(title: str | None) -> tuple[int, int] | None:
    """Primer episodio del release, manteniendo la API histórica."""
    parsed = parse_release(title)
    if not parsed.episodes:
        return None
    return int(parsed.season or 0), int(parsed.episodes[0])


def season_of(title: str | None) -> int | None:
    return _season_of_text(title or "")


def episode_in_release(
    title: str | None, season: int, episode: int
) -> bool:
    parsed = parse_release(title)
    if not parsed.episodes:
        return False
    return parsed.season == int(season) and int(episode) in parsed.episodes


def pack_matches_season(title: str | None, season: int) -> bool:
    """Un pack solo vale para su temporada, salvo que sea de serie completa.

    Un pack sin temporada identificable se considera genérico: puede ser una
    serie completa, una miniserie o un pack que el tracker no etiqueta. Los
    que sí declaran temporada no se mezclan con otras.
    """
    parsed = parse_release(title)
    if parsed.episodes:
        return False
    if parsed.complete_series or parsed.season is None:
        return True
    return parsed.season == int(season or 0)
