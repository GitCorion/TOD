"""Ajustes de búsqueda editables desde la web.

Hoy contiene una sola opción: buscar en Radarr/Sonarr los títulos
monitorizados que no tienen fichero ni están en la cola del *arr, como si
hubiera perdido el archivo. No modifica Radarr/Sonarr: solo marca esos
títulos para que TOD busque fuentes y publique sus `.strm`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import settings

ARR_FALLBACK_KEY = "search.arr_fallback"
DEFAULT_ARR_FALLBACK = False


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def from_values(arr_fallback: Any = None) -> dict[str, Any]:
    return {"arr_fallback": _bool(arr_fallback, DEFAULT_ARR_FALLBACK)}


def load(db_path: str | Path) -> dict[str, Any]:
    return from_values(settings.get_setting(db_path, ARR_FALLBACK_KEY))


def save(db_path: str | Path, *, arr_fallback: Any = None) -> dict[str, Any]:
    values = from_values(arr_fallback)
    settings.set_setting(
        db_path, ARR_FALLBACK_KEY, "1" if values["arr_fallback"] else "0"
    )
    return values
