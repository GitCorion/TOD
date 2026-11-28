"""Avisos puntuales a Jellyfin cuando TOD crea o quita `.strm`.

No se lanza un escaneo global: se notifica la ruta concreta para que Jellyfin
la procese en su biblioteca.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .jellyfin_leases import _get_sessions, key_from_strm

DEFAULT_TIMEOUT = 20.0
SESSIONS_TTL = 2.0
_PLAYING_CACHE: tuple[float, set[str]] | None = None


class JellyfinError(RuntimeError):
    pass


def _post(config: Config, path: str, payload: dict[str, Any]) -> Any:
    if not config.jellyfin_api_key:
        raise JellyfinError("sin API key de Jellyfin")
    url = f"{config.jellyfin_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f'MediaBrowser Token="{config.jellyfin_api_key}"',
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "tod-jellyfin/0.1",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=DEFAULT_TIMEOUT
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise JellyfinError(f"Jellyfin HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JellyfinError(f"Jellyfin no responde: {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def notify_updated(
    config: Config,
    paths: Iterable[str | Path],
    *,
    update_type: str = "Created",
) -> dict[str, Any]:
    """Notifica a Jellyfin rutas nuevas, modificadas o borradas."""
    clean = [str(path) for path in paths if str(path)]
    if not clean:
        return {"ok": False, "reason": "sin_rutas"}
    if not config.jellyfin_api_key:
        return {"ok": False, "reason": "sin_api_key"}
    try:
        _post(
            config,
            "/Library/Media/Updated",
            {
                "Updates": [
                    {"Path": path, "UpdateType": update_type}
                    for path in clean
                ]
            },
        )
    except JellyfinError as exc:
        return {"ok": False, "reason": str(exc)[:200]}
    return {"ok": True, "paths": len(clean), "update_type": update_type}


def playing_keys(
    config: Config,
    *,
    sessions: list[dict] | None = None,
    timeout: float = 3.0,
) -> set[str]:
    """Claves del catálogo que Jellyfin está reproduciendo ahora.

    Sirve para distinguir una reproducción real de un escaneo/ffprobe: los
    escaneos no tienen `NowPlayingItem`.
    """
    global _PLAYING_CACHE
    if sessions is None:
        now = time.monotonic()
        if _PLAYING_CACHE is not None and now - _PLAYING_CACHE[0] < SESSIONS_TTL:
            return set(_PLAYING_CACHE[1])
        sessions = _get_sessions(
            config.jellyfin_url, config.jellyfin_api_key or "", timeout=timeout
        )
    keys: set[str] = set()
    for session in sessions:
        item = session.get("NowPlayingItem") or {}
        path = item.get("Path")
        if not path:
            continue
        key = key_from_strm(path, db_path=config.db_path)
        if key:
            keys.add(key)
    if sessions is not None:
        _PLAYING_CACHE = (time.monotonic(), set(keys))
    return keys
