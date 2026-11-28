"""Inventario real de ficheros `.strm` publicados.

El panel on-demand cuenta lo que existe en disco, no lo que la base cree que
existe. Se cachea unos minutos y se invalida al escribir `.strm`.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .config import Config
from .strm import direct_path_map, strm_identity, strm_path

DEFAULT_TTL = 300.0
_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _strm_query(path: Path) -> dict[str, list[str]]:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")[:2000].strip()
    except OSError:
        return {}
    if not content:
        return {}
    try:
        parsed = urllib.parse.urlparse(content)
        return urllib.parse.parse_qs(parsed.query)
    except Exception:
        return {}


def _key_from_strm(path: Path, db_path: Path | str) -> str | None:
    value = _strm_query(path).get("key")
    if value:
        return value[0]
    return strm_identity(db_path, path)[0]


def _scan(root: Path, db_path: Path | str) -> dict[str, Any]:
    # El mapa de rutas solo se necesita si aparece un .strm directo.
    path_map: dict[str, tuple[str, int | None]] | None = None
    paths: set[str] = set()
    keys: set[str] = set()
    movies = 0
    episodes = 0
    versions = 0
    series: set[str] = set()

    for base, kind in (("movies", "movie"), ("series", "series")):
        directory = root / base
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.strm"):
            query = _strm_query(path)
            if not query and path_map is None:
                path_map = direct_path_map(db_path)
            key, _ = strm_identity(db_path, path, path_map=path_map)
            # Las versiones llevan &release=<id>; en modo directo el contenido
            # es una ruta, así que se reconoce por el sufijo del nombre. El
            # principal también puede mapear a un release, por eso no basta
            # con mirar `release_id`.
            direct_version = (
                not query
                and " - " in path.stem
                and any(
                    sibling.stem == path.stem.split(" - ", 1)[0]
                    for sibling in path.parent.glob("*.strm")
                )
            )
            if query.get("release") or direct_version:
                versions += 1
                continue
            paths.add(str(path))
            if key:
                keys.add(key)
            if kind == "movie":
                movies += 1
            else:
                episodes += 1
                # series/<Título>/Season XX/<episodio>.strm
                series.add(str(path.parent.parent))

    return {
        "root": str(root),
        "paths": paths,
        "keys": keys,
        "movies": movies,
        "series": len(series),
        "episodes": episodes,
        "versions": versions,
        "scanned_at": time.time(),
    }


def inventory(
    root: str | Path,
    *,
    ttl: float = DEFAULT_TTL,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    key = str(root)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached["monotonic"] < ttl:
            return cached
        if db_path is None:
            db_path = Config.from_env().db_path
        data = _scan(root, db_path)
        data["monotonic"] = now
        _cache[key] = data
        return data


def invalidate(root: str | Path | None = None) -> None:
    with _lock:
        if root is None:
            _cache.clear()
        else:
            _cache.pop(str(Path(root)), None)


def is_published(
    root: str | Path, item: dict[str, Any], paths: set[str]
) -> bool:
    try:
        return str(strm_path(Path(root), item)) in paths
    except Exception:
        return False


def is_key_published(item: dict[str, Any], keys: set[str]) -> bool:
    return bool(item.get("key")) and item["key"] in keys


def summary(
    root: str | Path, *, db_path: Path | str | None = None
) -> dict[str, Any]:
    data = inventory(root, db_path=db_path)
    return {
        "root": data["root"],
        "movies": data["movies"],
        "series": data["series"],
        "episodes": data["episodes"],
        "versions": data["versions"],
        "scanned_at": data["scanned_at"],
    }
