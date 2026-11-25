"""SQLite: conexión, migraciones y transacciones.

- El catálogo nuevo es WAL y con foreign_keys.
- La base legacy se lee en modo inmutable: nunca se escribe.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


def connect(path: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        uri = f"file:{path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=20)
    else:
        conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect_legacy(path: Path | str) -> sqlite3.Connection:
    """Lee la assets.db existente sin escribirla ni exigir permisos de WAL.

    immutable=1 evita que SQLite intente crear -shm/-wal. Es correcto para
    una copia de referencia o para una base que no está siendo escrita.
    """
    return connect(path, readonly=True)


def migrate(conn: sqlite3.Connection, migrations_dir: Path | str) -> list[str]:
    migrations_dir = Path(migrations_dir)
    conn.executescript(SCHEMA_MIGRATIONS)
    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations")
    }
    newly: list[str] = []
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        version = sql_file.stem
        if version in applied:
            continue
        script = sql_file.read_text(encoding="utf-8")
        # Una migración que gestiona su propia transacción (reconstrucción de
        # tablas) se ejecuta tal cual. El resto se envuelve para que el DDL y
        # el registro de versión sean atómicos: si falla, no queda a medias.
        manages_transaction = bool(
            re.search(r"(?im)^\s*BEGIN(?:\s+TRANSACTION)?\b", script)
        )
        try:
            if manages_transaction:
                conn.executescript(script)
            else:
                conn.executescript("BEGIN IMMEDIATE;\n" + script)
            conn.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        newly.append(version)
    return newly


def init_db(path: Path | str, migrations_dir: Path | str) -> list[str]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        versions = migrate(conn, migrations_dir)
    finally:
        conn.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return versions


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "media_items",
        "releases",
        "torrents",
        "torrent_files",
        "play_targets",
        "tracker_policies",
        "seed_commitments",
        "play_intents",
    )
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
