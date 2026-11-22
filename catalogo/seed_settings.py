"""Ajustes de siembra editables desde la web.

TOD garantiza el mínimo: fuerza el seed hasta cumplir horas/ratio y después
suelta el torrent para que qBittorrent aplique sus propias reglas. El borrado
desde TOD es opcional y viene desactivado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import settings

MIN_HOURS_KEY = "seed.min_hours"
RATIO_KEY = "seed.ratio"
FORCE_KEY = "seed.force"
ALLOW_DELETE_KEY = "seed.allow_delete"

DEFAULT_MIN_HOURS = 72
DEFAULT_RATIO = 1.0
DEFAULT_FORCE = True
DEFAULT_ALLOW_DELETE = False


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def from_values(
    min_hours: Any = None,
    ratio: Any = None,
    force: Any = None,
    allow_delete: Any = None,
) -> dict[str, Any]:
    hours = max(0, min(24 * 365, int(_float(min_hours, DEFAULT_MIN_HOURS))))
    target = max(0.0, min(100.0, _float(ratio, DEFAULT_RATIO)))
    return {
        "min_hours": hours,
        "ratio": target,
        "force": _bool(force, DEFAULT_FORCE),
        "allow_delete": _bool(allow_delete, DEFAULT_ALLOW_DELETE),
    }


def from_conn(conn: Any) -> dict[str, Any]:
    rows = {
        row["key"]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
            (MIN_HOURS_KEY, RATIO_KEY, FORCE_KEY, ALLOW_DELETE_KEY),
        )
    }
    return from_values(
        rows.get(MIN_HOURS_KEY),
        rows.get(RATIO_KEY),
        rows.get(FORCE_KEY),
        rows.get(ALLOW_DELETE_KEY),
    )


def load(db_path: str | Path) -> dict[str, Any]:
    return from_values(
        settings.get_setting(db_path, MIN_HOURS_KEY),
        settings.get_setting(db_path, RATIO_KEY),
        settings.get_setting(db_path, FORCE_KEY),
        settings.get_setting(db_path, ALLOW_DELETE_KEY),
    )


def save(
    db_path: str | Path,
    *,
    min_hours: Any,
    ratio: Any,
    force: Any,
    allow_delete: Any = None,
) -> dict[str, Any]:
    values = from_values(min_hours, ratio, force, allow_delete)
    settings.set_setting(db_path, MIN_HOURS_KEY, str(values["min_hours"]))
    settings.set_setting(db_path, RATIO_KEY, str(values["ratio"]))
    settings.set_setting(db_path, FORCE_KEY, "1" if values["force"] else "0")
    settings.set_setting(
        db_path, ALLOW_DELETE_KEY, "1" if values["allow_delete"] else "0"
    )
    return values
