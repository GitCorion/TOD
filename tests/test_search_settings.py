import sqlite3
from pathlib import Path

from catalogo import settings
from catalogo.db import init_db, migrate
from catalogo.search_settings import load, save

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def test_search_fallback_roundtrip(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    assert load(str(db))["arr_fallback"] is False
    values = save(str(db), arr_fallback=True)
    assert values == {"arr_fallback": True}
    assert load(str(db))["arr_fallback"] is True
    assert settings.get_setting(str(db), "search.arr_fallback") == "1"


def test_migration_moves_old_seed_key(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value) "
        "VALUES('seed.arr_fallback','1')"
    )
    conn.execute(
        "DELETE FROM schema_migrations WHERE version='041_search_arr_fallback'"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    migrate(conn, MIGRATIONS)
    conn.close()

    assert load(str(db))["arr_fallback"] is True
    assert settings.get_setting(str(db), "seed.arr_fallback") is None
