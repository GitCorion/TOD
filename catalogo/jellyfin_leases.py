"""Renueva leases de reproducción a partir de las sesiones de Jellyfin.

El proxy renueva el lease cuando recibe un Range de `/play`, pero si el
cliente acumula búfer o pausa la red, el lease puede caducar. El mover de
1fichier podría mover un pack mientras se está viendo otro episodio.

Este vigilante consulta `/Sessions` y, para cada `.strm` de catálogo en
reproducción, renueva el lease de esa clave. Así el mover ve actividad
aunque el player no esté pidiendo rangos en ese instante.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import stream_leases


def _now() -> datetime:
    return datetime.now(timezone.utc)


def key_from_strm(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if candidate.suffix.lower() != ".strm":
        return None
    try:
        text = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parsed = urlparse(text)
    if parsed.path.rstrip("/") != "/play":
        return None
    params = parse_qs(parsed.query)
    key = (params.get("key") or [""])[0].strip()
    return key or None


def _get_sessions(
    base_url: str, api_key: str, *, timeout: float = 10.0
) -> list[dict]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/Sessions",
        headers={
            "Authorization": f'MediaBrowser Token="{api_key}"',
            "Accept": "application/json",
            "User-Agent": "catalogo-leases/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Jellyfin HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Jellyfin no responde: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Jellyfin devolvió JSON inválido") from exc
    return payload if isinstance(payload, list) else []


def touch_active_leases(
    db_path,
    *,
    base_url: str = "http://127.0.0.1:8086",
    api_key: str,
    timeout: float = 10.0,
    now: datetime | None = None,
    sessions: list[dict] | None = None,
) -> dict[str, Any]:
    current = now or _now()
    if sessions is None:
        sessions = _get_sessions(base_url, api_key, timeout=timeout)
    touched: list[dict[str, Any]] = []
    skipped: int = 0
    for session in sessions:
        item = session.get("NowPlayingItem") or {}
        key = key_from_strm(item.get("Path"))
        if not key:
            skipped += 1
            continue
        session_id = (
            f"jellyfin:{session.get('Id') or session.get('DeviceId') or 'unknown'}"
        )
        stream_leases.touch(
            db_path,
            key,
            session_id,
            client_ip=session.get("RemoteEndPoint"),
            user_agent=session.get("Client"),
            now=current,
        )
        touched.append(
            {
                "key": key,
                "session": session_id,
                "item": item.get("Name"),
                "position_ticks": (session.get("PlayState") or {}).get(
                    "PositionTicks"
                ),
            }
        )
    return {
        "sessions": len(sessions),
        "touched": touched,
        "skipped": skipped,
    }
