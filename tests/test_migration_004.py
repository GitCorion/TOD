import shutil
import sqlite3
from pathlib import Path

from catalogo.db import connect, init_db, migrate

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def test_migration_004_from_previous_schema(tmp_path):
    old = tmp_path / "old_migrations"
    old.mkdir()
    for name in ("001_init.sql", "002_download_intents.sql", "003_health_live_fields.sql"):
        shutil.copy(MIGRATIONS / name, old / name)
    db = tmp_path / "catalogo.db"
    init_db(db, old)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO assets(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM assets").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes) VALUES(?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, state) VALUES(?,?,?)",
        ("hash1", release_id, "seeding"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO files(asset_id,release_id,torrent_id,file_index,local_path,"
        "size_bytes,state) VALUES(?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "/tmp/test.mkv", 1000, "ready"),
    )
    file_id = conn.execute("SELECT id FROM files").fetchone()[0]
    conn.execute(
        "INSERT INTO play_targets(asset_id, release_id, file_id, source_kind) "
        "VALUES(?,?,?,?)",
        (asset_id, release_id, file_id, "local_seed"),
    )
    conn.execute(
        "INSERT INTO download_intents(asset_id,release_id,tracker,size_bytes,state) "
        "VALUES(?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", 1000, "approved"),
    )
    conn.commit()
    conn.close()

    conn = connect(db)
    applied = migrate(conn, MIGRATIONS)
    conn.close()
    assert "004_on_demand" in applied

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"media_items", "torrent_files", "seed_commitments", "play_intents"} <= tables
    assert "assets" not in tables
    assert "files" not in tables

    media = conn.execute("SELECT * FROM media_items").fetchone()
    assert media["media_id"] == "movie:tmdb:1"
    assert media["status"] == "SEEDING_LOCAL"
    assert media["active_file_id"] == file_id

    file_row = conn.execute("SELECT * FROM torrent_files").fetchone()
    assert file_row["storage_location"] == "LOCAL_DISK"

    intent = conn.execute("SELECT * FROM seed_commitments").fetchone()
    assert intent["state"] == "approved"
    assert conn.execute("SELECT COUNT(*) FROM play_intents").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
