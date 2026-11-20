"""Inventario real de ficheros `.strm` publicados.

El panel on-demand cuenta lo que existe en disco, no lo que la base cree que
existe. Se cachea unos segundos para no recorrer el árbol en cada petición.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .strm import strm_path

DEFAULT_TTL = 30.0
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


def _key_from_strm(path: Path) -> str | None:
    value = _strm_query(path).get("key")
    return value[0] if value else None


def _scan(root: Path) -> dict[str, Any]:
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
            # Las versiones llevan &release=<id>; el .strm principal no.
            if query.get("release"):
                versions += 1
                continue
            paths.add(str(path))
            key = (query.get("key") or [None])[0]
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


def inventory(root: str | Path, *, ttl: float = DEFAULT_TTL) -> dict[str, Any]:
    root = Path(root)
    key = str(root)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached["monotonic"] < ttl:
            return cached
        data = _scan(root)
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


def summary(root: str | Path) -> dict[str, Any]:
    data = inventory(root)
    return {
        "root": data["root"],
        "movies": data["movies"],
        "series": data["series"],
        "episodes": data["episodes"],
        "versions": data["versions"],
        "scanned_at": data["scanned_at"],
    }
