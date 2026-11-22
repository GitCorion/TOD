"""Preferencia de idioma para elegir fuentes y publicar `.strm`.

No traduce ni filtra el catálogo. Distingue castellano de España y latino:
el latino solo se acepta como V.O. cuando el título es originalmente español;
un doblaje latino de una película inglesa no cuenta como español.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import settings

KEY = "language.preference"
DEFAULT = "any"
CHOICES = ("any", "castellano_first", "only_castellano")
LEGACY = {
    "prefer_spanish": "castellano_first",
    "only_spanish": "only_castellano",
}


def load(db_path: str | Path) -> str:
    value = str(settings.get_setting(db_path, KEY) or DEFAULT).strip().lower()
    value = LEGACY.get(value, value)
    return value if value in CHOICES else DEFAULT


def save(db_path: str | Path, value: str) -> str:
    value = LEGACY.get(str(value or "").strip().lower(), str(value or "").strip().lower())
    if value not in CHOICES:
        raise ValueError(f"idioma no válido: {value}")
    settings.set_setting(db_path, KEY, value)
    return value


def _is_spanish_original(original_language: str | None) -> bool:
    return (original_language or "").strip().lower() in (
        "es",
        "spa",
        "spanish",
        "español",
        "espanol",
    )


def rank(
    audio: str | None,
    preference: str,
    original_language: str | None = None,
) -> int | None:
    """0 castellano, 1 V.O. española, 2 VOSE, 3 desconocido, 4 otro.

    ``None`` significa excluido. El latino doblado nunca entra; el latino
    original en español se acepta como V.O.
    """
    if preference == "any":
        return 0
    tag = (audio or "unknown").strip().lower()
    if preference == "only_castellano":
        return 0 if tag in ("castellano", "espanol") else None
    if tag == "latino":
        if not _is_spanish_original(original_language):
            return None
        return 1
    if tag in ("castellano", "espanol"):
        return 0
    if tag == "spanish_unverified":
        return 2
    if tag == "vose":
        return 2
    if tag in ("", "unknown"):
        return 3
    return 4
