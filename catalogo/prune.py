"""Poda los `.strm` de títulos que ya están en Radarr/Sonarr."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .db import connect
from .library_index import build_library_index, in_library
from .strm import strm_path


def _remove_empty(parent: Path, stop: Path) -> None:
    while parent != stop and parent.is_dir() and not any(parent.iterdir()):
        if parent.parent == stop and parent.name in ("movies", "series"):
            break
        parent.rmdir()
        parent = parent.parent


def prune_library(
    db_path,
    out_dir,
    *,
    radarr=None,
    sonarr=None,
    dry_run: bool = True,
) -> dict:
    index = build_library_index(radarr, sonarr)
    root = Path(out_dir)
    assets_in_library = 0
    removed: list[str] = []
    with closing(connect(db_path)) as conn:
        assets = conn.execute("SELECT * FROM media_items").fetchall()
    for row in assets:
        asset = dict(row)
        if not in_library(index, asset):
            continue
        assets_in_library += 1
        default = strm_path(root, asset)
        paths = [default] + list(
            default.parent.glob(f"{default.stem} - *.strm")
        )
        for path in paths:
            if not path.exists():
                continue
            if not dry_run:
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
            removed.append(str(path))
        if not dry_run:
            _remove_empty(default.parent, root)
    return {
        "out": str(root),
        "assets_in_library": assets_in_library,
        "removed": removed,
        "removed_count": len(removed),
        "dry_run": dry_run,
    }
