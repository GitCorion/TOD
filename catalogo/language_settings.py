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


LATAM = {
    "AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "GT", "HN",
    "MX", "NI", "PA", "PE", "PR", "PY", "SV", "US", "UY", "VE",
}


def _codes(value: str | None) -> set[str]:
    return {
        code.strip().upper()
        for code in str(value or "").split(",")
        if code.strip()
    }


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
    original_country: str | None = None,
) -> int | None:
    """0 V.O. preferente, 1 doblaje aceptable, 2 VOSE, 3 desconocido, 4 otro.

    ``None`` significa excluido. Si el título es originalmente español y el
    país es latinoamericano, el latino es la V.O. y el castellano es doblaje.
    Si es español de España, el latino doblado queda fuera.
    """
    if preference == "any":
        return 0
    tag = (audio or "unknown").strip().lower()
    if preference == "only_castellano":
        return 0 if tag in ("castellano", "espanol") else None
    if preference == "vose_first":
        if tag == "vose":
            return 0
        if tag in ("", "unknown", "english"):
            return 1
        if tag in ("castellano", "espanol"):
            return 2
        if tag == "latino":
            if (
                _is_spanish_original(original_language)
                and "ES" not in _codes(original_country)
                and (not _codes(original_country) or bool(_codes(original_country) & LATAM))
            ):
                return 1
            return None
        return 3
    spanish_original = _is_spanish_original(original_language)
    countries = _codes(original_country)
    # España manda si participa: una coproducción hispano-mexicana sigue
    # siendo castellano. El latino solo es V.O. si no hay producción española.
    latam_original = (
        spanish_original
        and "ES" not in countries
        and bool(countries & LATAM)
    )
    if tag == "latino":
        if latam_original:
            return 0
        if spanish_original and not countries:
            # Sin país no se puede demostrar que sea doblaje: se acepta
            # como V.O. pero después del castellano.
            return 1
        return None
    if tag in ("castellano", "espanol"):
        return 1 if latam_original else 0
    if tag in ("spanish_unverified", "vose"):
        return 2
    if tag in ("", "unknown"):
        return 3
    return 4
