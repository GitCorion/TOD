"""Traduce los mensajes del tracker a veredictos accionables.

qBittorrent ya recibe estos mensajes en cada announce; el proxy no los
miraba. Distinguirlos importa:

- `unregistered`: el tracker ya no conoce ese torrent; sembrarlo no suma.
- `client_not_allowed`: el cliente o su versión no está en la whitelist.
  No se arregla con código: hay que cambiar de versión o pedir el alta.
- `not_authorized`: passkey o cuenta sin permiso.
"""

from __future__ import annotations

from typing import Iterable

UNREGISTERED = (
    "unregistered torrent",
    "torrent not registered",
    "not registered",
    "torrent not found",
    "no such torrent",
)
CLIENT_NOT_ALLOWED = (
    "client not allowed",
    "client is not allowed",
    "unrecognized client",
    "unknown client",
    "client not accepted",
    "client is banned",
    "not a valid client",
)
NOT_AUTHORIZED = (
    "not authorized",
    "unauthorized",
    "invalid passkey",
    "passkey",
    "account banned",
    "banned",
)


def classify(messages: Iterable[str]) -> tuple[str | None, str | None]:
    """Devuelve (veredicto, mensaje) del primer problema reconocible."""
    cleaned = [str(message).strip() for message in messages if str(message).strip()]
    if not cleaned:
        return None, None
    text = " ".join(cleaned).lower()
    for pattern in CLIENT_NOT_ALLOWED:
        if pattern in text:
            return "client_not_allowed", " | ".join(cleaned)
    for pattern in UNREGISTERED:
        if pattern in text:
            return "unregistered", " | ".join(cleaned)
    for pattern in NOT_AUTHORIZED:
        if pattern in text:
            return "not_authorized", " | ".join(cleaned)
    return None, None
