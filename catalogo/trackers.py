"""Idioma esperado y prioridad de trackers.

Los trackers no se declaran a mano: se descubren de Prowlarr (nombre, idioma,
privacidad y estado) y de Jackett (`all`, con el nombre real en cada resultado).
Así el repositorio público no contiene membresías ni nombres privados.

- Prowlarr aporta `language` (por ejemplo `es-ES`) para inferir castellano.
- Jackett aporta el indexer real de cada resultado en `<jackettindexer>`.
- Si no se pasa una lista explícita, se usan todos los indexers habilitados.
"""

from __future__ import annotations

from typing import Any, Iterable

from .public_sync import audio_of

# Compatibilidad: listas vacías. El descubrimiento real viene de los clientes.
SPANISH_TRACKERS: frozenset[str] = frozenset()
INTERNATIONAL_TRACKERS: frozenset[str] = frozenset()
SPORTS_TRACKERS: frozenset[str] = frozenset()
LAST_PRIORITY_TRACKERS: frozenset[str] = frozenset()
JACKETT_TRACKERS: dict[str, str] = {}
GENERAL_TRACKERS: tuple[str, ...] = ()


def spanish_trackers_from_indexers(
    indexers: Iterable[dict[str, Any]] | None,
) -> frozenset[str]:
    """Nombres de indexers cuyo idioma declarado es español."""
    names: set[str] = set()
    for item in indexers or ():
        if item.get("enable") is False:
            continue
        name = str(item.get("name") or "").strip()
        language = str(item.get("language") or "").strip().lower()
        if name and language.startswith("es"):
            names.add(name)
    return frozenset(names)


def tracker_priority(tracker: str | None) -> int:
    """Reservado para prioridades locales; por defecto todos empatan."""
    return 1 if tracker in LAST_PRIORITY_TRACKERS else 0


def audio_for(
    title: str | None,
    tracker: str | None,
    *,
    spanish_trackers: Iterable[str] | None = None,
) -> tuple[str, str | None]:
    """Devuelve (audio, confianza).

    La etiqueta del título manda. Si no hay etiqueta, un indexer cuyo idioma
    declara español implica castellano con confianza ``tracker``.
    """
    tag = audio_of(title)
    if tag != "unknown":
        return tag, "title"
    known = (
        frozenset(spanish_trackers)
        if spanish_trackers is not None
        else SPANISH_TRACKERS
    )
    if tracker in known:
        return "castellano", "tracker"
    return "unknown", None
