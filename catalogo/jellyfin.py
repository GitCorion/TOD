"""Avisos puntuales a Jellyfin cuando TOD crea o quita `.strm`.

No se lanza un escaneo global: se notifica la ruta concreta para que Jellyfin
la procese en su biblioteca.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .config import Config

DEFAULT_TIMEOUT = 20.0


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
