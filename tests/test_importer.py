import sqlite3
from pathlib import Path

from catalogo.importer import import_legacy

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


LEGACY_SCHEMA = """
CREATE TABLE assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    tmdb INTEGER,
    season INTEGER,
    episode INTEGER,
    title TEXT,
    year INTEGER
);
CREATE TABLE releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    catalog_release_id INTEGER UNIQUE,
    tracker TEXT,
    title TEXT,
    download_url TEXT,
    quality TEXT,
    audio TEXT,
    seeders INTEGER,
    size_bytes INTEGER,
    info_hash TEXT
);
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    release_id INTEGER,
    tier TEXT,
    path TEXT,
    qbit_hash TEXT,
    qbit_file_index INTEGER,
    size_bytes INTEGER,
    quality TEXT,
    audio TEXT,
    state TEXT,
    last_seen TEXT
);
"""


def make_legacy(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.executemany(
        "INSERT INTO assets(id,key,type,tmdb,season,episode,title,year) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [
            (1, "movie:9737", "movie", 9737, None, None, "Bad Boys 1995", 1995),
            (2, "movie:0", "movie", 0, None, None, "Dune 2021", 2021),
            (3, "series:2190:29:4", "series", 2190, 29, 4, "south park s29e04", 2025),
        ],
    )
    conn.executemany(
        "INSERT INTO releases(id,asset_id,tracker,title,download_url,quality,audio,"
        "seeders,size_bytes,info_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (1, 1, "TrackerEspanol2 (API)", "Bad.Boys.1995.mkv", "http://prowlarr/1", "1080p", "castellano", 50, 1000, None),
            (2, 2, "TrackerEspanol (API)", "Dune.2021.mkv", "http://prowlarr/2", "4K", "unknown", 10, 2000, None),
            (3, 3, "TrackerInternacional (API)", "South.Park.S29E04.mkv", "http://prowlarr/3", "1080p", "spanish_unverified", 30, 3000, "abc"),
        ],
    )
    conn.executemany(
        "INSERT INTO files(id,asset_id,release_id,tier,path,qbit_hash,qbit_file_index,"
        "size_bytes,quality,audio,state,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, 1, 1, "library", "/mnt/fichier/Bad Boys.mkv", None, None, 1000, "1080p", "castellano", "archived", None),
            (2, 3, 3, "local_seed", "/mnt/descargas/South.Park.S29E04.mkv", "abc", 2, 3000, "1080p", "spanish_unverified", "seeding", None),
            (3, 2, 2, "downloading", None, None, None, 2000, "4K", "unknown", "downloading", None),
        ],
    )
    conn.commit()
    conn.close()


def test_import_is_non_destructive_and_idempotent(tmp_path):
    source = tmp_path / "legacy.db"
    dest = tmp_path / "catalogo.db"
    make_legacy(source)

    first = import_legacy(source, dest, MIGRATIONS)
    assert first.assets_seen == 3
    assert first.assets_inserted == 3
    assert first.needs_identity == 1
    assert first.files_inserted == 3
    assert first.targets_inserted == 3

    conn = sqlite3.connect(dest)
    conn.row_factory = sqlite3.Row
    keys = {row["key"] for row in conn.execute("SELECT key FROM media_items")}
    assert keys == {
        "movie:tmdb:9737",
        "movie:legacy:2",
        "series:tmdb:2190:29:4",
    }
    legacy_status = conn.execute(
        "SELECT identity_status FROM media_items WHERE key='movie:legacy:2'"
    ).fetchone()["identity_status"]
    assert legacy_status == "needs_identity"

    targets = {
        row["key"]: (row["source_kind"], row["file_id"], row["release_id"])
        for row in conn.execute(
            "SELECT a.key, p.source_kind, p.file_id, p.release_id "
            "FROM play_targets p JOIN media_items a ON a.id=p.asset_id"
        )
    }
    assert targets["movie:tmdb:9737"][0] == "library"
    assert targets["series:tmdb:2190:29:4"][0] == "local_seed"
    assert targets["movie:legacy:2"][0] == "torrent"
    assert targets["movie:legacy:2"][2] == 2

    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM torrent_files").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 1
    assert conn.execute(
        "SELECT state FROM torrents WHERE info_hash='abc'"
    ).fetchone()["state"] == "seeding"
    conn.close()

    second = import_legacy(source, dest, MIGRATIONS)
    assert second.assets_inserted == 0
    assert second.releases_inserted == 0
    assert second.files_inserted == 0
    assert second.targets_inserted == 0

    conn = sqlite3.connect(dest)
    assert conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM torrent_files").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM play_targets").fetchone()[0] == 3
    conn.close()

    src = sqlite3.connect(source)
    assert src.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
    src.close()
