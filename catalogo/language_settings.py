"""Preferencia de idioma para elegir fuentes y publicar `.strm`.

No traduce ni filtra el catálogo: decide qué release es preferible cuando hay
varias versiones del mismo título. Las fuentes sin idioma verificado no se
consideran españolas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import settings

KEY = "language.preference"
DEFAULT = "prefer_spanish"
CHOICES = ("any", "prefer_spanish", "only_spanish")

SPANISH = {
    "castellano",
    "latino",
    "espanol",
    "spanish",
    "spanish_unverified",
}


def load(db_path: str | Path) -> str:
    value = str(settings.get_setting(db_path, KEY) or DEFAULT).strip().lower()
    return value if value in CHOICES else DEFAULT


def save(db_path: str | Path, value: str) -> str:
    value = str(value or "").strip().lower()
    if value not in CHOICES:
        raise ValueError(f"idioma no válido: {value}")
    settings.set_setting(db_path, KEY, value)
    return value


def rank(audio: str | None, preference: str) -> int | None:
    """0 español, 1 desconocido, 2 otro idioma. None = excluido."""
    if preference == "any":
        return 0
    tag = (audio or "unknown").strip().lower()
    if tag in SPANISH:
        return 0
    if preference == "only_spanish":
        return None
    if tag in ("", "unknown"):
        return 1
    return 2
