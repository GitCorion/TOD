"""Sondas mínimas sobre el fichero descargado (audio, idioma).

Se usan solo para decidir si una fuente sirve para el catálogo en español.
No modifican el fichero ni el torrent.

Importante: un idioma no declarado (``und`` o vacío) **no** se considera
prueba de que la fuente no sea española. Solo se descarta cuando todas las
pistas de audio tienen un idioma declarado y ninguna es española.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SPANISH_CODES = {
    "spa", "es", "es-es", "es_es", "spanish", "castellano", "espanol",
    "español", "latino", "lat",
}
SPANISH_WORDS = ("castellano", "español", "espanol", "spanish", "latino")
UNKNOWN_CODES = {"", "und", "unknown", "none"}


@dataclass(frozen=True)
class AudioProbe:
    languages: tuple[str, ...]
    has_unknown: bool = False

    @property
    def spanish(self) -> bool:
        return any(
            item in SPANISH_CODES
            or item.startswith("es-")
            or item.startswith("es_")
            for item in self.languages
        )

    @property
    def conclusive(self) -> bool:
        return bool(self.languages) and not self.has_unknown


def _run_ffprobe(path: Path, timeout: float) -> dict | None:
    binary = shutil.which("ffprobe") or "ffprobe"
    command = [
        binary, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index:stream_tags=language,title",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(3.0, timeout),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def probe_audio(
    path: str | Path | None, *, timeout: float = 15.0
) -> AudioProbe | None:
    """Devuelve un sondeo de audio, o None si no se pudo hacer."""
    if not path:
        return None
    file_path = Path(path)
    if not file_path.is_file():
        return None
    payload = _run_ffprobe(file_path, timeout)
    if payload is None:
        return None
    languages: list[str] = []
    has_unknown = False
    for stream in payload.get("streams") or []:
        tags = stream.get("tags") or {}
        language = str(tags.get("language") or "").strip().lower()
        title = str(tags.get("title") or "").strip().lower()
        if language not in UNKNOWN_CODES:
            languages.append(language)
        else:
            has_unknown = True
        if any(word in title for word in SPANISH_WORDS):
            languages.append("spa")
    if not languages and not has_unknown:
        return None
    return AudioProbe(tuple(dict.fromkeys(languages)), has_unknown)


def audio_verdict(probe: AudioProbe | None) -> bool | None:
    """True español, False no español concluyente, None desconocido.

    Con una sola pista sin idioma declarado, el resultado es None: no se
    puede afirmar que sea inglesa/francesa.
    """
    if probe is None:
        return None
    if probe.spanish:
        return True
    if probe.conclusive:
        return False
    return None


def probe_audio_languages(
    path: str | Path | None, *, timeout: float = 15.0
) -> list[str] | None:
    probe = probe_audio(path, timeout=timeout)
    if probe is None:
        return None
    return list(probe.languages) or None


def has_spanish(languages: list[str] | None) -> bool:
    if not languages:
        return False
    return any(
        item in SPANISH_CODES
        or item.startswith("es-")
        or item.startswith("es_")
        for item in languages
    )
