import sqlite3

import pytest

from catalogo.db import connect, migrate


def test_failed_migration_rolls_back(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_ok.sql").write_text(
        "CREATE TABLE ok(id INTEGER);", encoding="utf-8"
    )
    (migrations / "002_bad.sql").write_text(
        "CREATE TABLE antes(id INTEGER);\nCREATE TABLE roto(;", encoding="utf-8"
    )

    conn = connect(tmp_path / "catalogo.db")
    try:
        with pytest.raises(sqlite3.Error):
            migrate(conn, migrations)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "ok" in tables
        assert "antes" not in tables
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        assert versions == {"001_ok"}
    finally:
        conn.close()
