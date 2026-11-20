"""Ajustes persistentes del catálogo on-demand."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .db import connect

ONDEMAND_MAX_ACTIVE = "ondemand_max_active"
DEFAULT_MAX_ACTIVE = 4
MIN_MAX_ACTIVE = 1
MAX_MAX_ACTIVE = 10


def get_setting(db_path: str | Path, key: str, default: str | None = None):
    try:
        with closing(connect(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.Error:
        return default
    return row["value"] if row is not None else default


def set_setting(db_path: str | Path, key: str, value: str) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, str(value)),
        )
        conn.commit()


def get_int(db_path: str | Path, key: str, default: int) -> int:
    value = get_setting(db_path, key)
    try:
        return int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


def ondemand_max_active(
    db_path: str | Path, default: int = DEFAULT_MAX_ACTIVE
) -> int:
    value = get_int(db_path, ONDEMAND_MAX_ACTIVE, default)
    return max(MIN_MAX_ACTIVE, min(MAX_MAX_ACTIVE, value))


def set_ondemand_max_active(db_path: str | Path, value: int) -> int:
    value = int(value)
    if value < MIN_MAX_ACTIVE or value > MAX_MAX_ACTIVE:
        raise ValueError(
            f"el límite debe estar entre {MIN_MAX_ACTIVE} y {MAX_MAX_ACTIVE}"
        )
    set_setting(db_path, ONDEMAND_MAX_ACTIVE, str(value))
    return value
