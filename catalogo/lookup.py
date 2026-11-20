"""Lookup de un .strm de Jellyfin para la app.

La APK recibe el `Path` del MediaSource y lo pasa aquí. El proxy valida que
sea un .strm dentro de las bibliotecas permitidas, extrae `key`/`release` y
devuelve el estado actual. No arranca nada.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

from .status import asset_status

DEFAULT_STRM_ROOTS = ("/mnt/catalogo", "/mnt/palantir")


class LookupQbit(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...


def _roots(roots: tuple[str, ...] | list[str] | None = None) -> list[Path]:
    if roots:
        values = roots
    else:
        env = os.environ.get("CATALOGO_STRM_ROOTS", "")
        values = (
            tuple(item for item in env.split(os.pathsep) if item)
            if env
            else DEFAULT_STRM_ROOTS
        )
    result: list[Path] = []
    for value in values:
        try:
            result.append(Path(value).expanduser().resolve())
        except OSError:
            continue
    return result


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def lookup_strm(
    db_path: str,
    path: str,
    *,
    qbit: LookupQbit | None = None,
    allowed_roots: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    raw = (path or "").strip()
    if not raw:
        return {"is_catalog": False, "reason": "sin_path"}
    candidate = Path(raw).expanduser()
    if candidate.suffix.lower() != ".strm":
        return {"is_catalog": False, "reason": "no_strm"}
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return {"is_catalog": False, "reason": "no_existe"}
    roots = _roots(allowed_roots)
    if roots and not any(_under(resolved, root) for root in roots):
        return {"is_catalog": False, "reason": "path_no_permitido"}
    try:
        text = resolved.read_text(encoding="utf-8").strip()
    except OSError:
        return {"is_catalog": False, "reason": "no_legible"}
    parsed = urlparse(text)
    params = parse_qs(parsed.query)
    key = (params.get("key") or [""])[0].strip()
    if not key:
        return {"is_catalog": False, "reason": "sin_key"}
    release_id: int | None = None
    if params.get("release"):
        try:
            release_id = int(params["release"][0])
        except (TypeError, ValueError):
            release_id = None
    status = asset_status(
        db_path, key, release_id=release_id, qbit=qbit
    )
    query: dict[str, Any] = {"key": key}
    if release_id is not None:
        query["release"] = release_id
    play_query = dict(query)
    play_query["download"] = 1
    return {
        "is_catalog": True,
        "key": key,
        "release_id": release_id,
        "path": str(resolved),
        "status_url": "/status?" + urlencode(query),
        "play_url": "/play?" + urlencode(play_query),
        "status": status,
    }
