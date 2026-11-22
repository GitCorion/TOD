import sqlite3
from pathlib import Path

from catalogo import language_settings
from catalogo.db import init_db
from catalogo.resolver import Resolver
from catalogo.strm import export_strm

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path, *, spanish_seeds=5, english_seeds=50, original_language=None):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,original_language) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020, original_language),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.executemany(
        "INSERT INTO releases(asset_id,tracker,title,audio,size_bytes,health,seeders) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (asset_id, "TrackerES", "Test.1080p.Castellano.mkv",
             "castellano", 2 * 1024**3, "alive", spanish_seeds),
            (asset_id, "Seedpool", "Test.1080p.ENGLISH.mkv",
             "unknown", 1024**3, "alive", english_seeds),
            (asset_id, "TrackerLatino", "Test.1080p.Latino.mkv",
             "latino", 900 * 1024**2, "alive", 99),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_rank_distinguishes_castellano_and_latino():
    assert language_settings.rank("castellano", "castellano_first") == 0
    assert language_settings.rank("unknown", "castellano_first") == 3
    assert language_settings.rank("vose", "castellano_first") == 2
    # Latino doblado: fuera. Latino V.O. de un título español: aceptado.
    assert language_settings.rank("latino", "castellano_first", "en") is None
    assert language_settings.rank("latino", "castellano_first", "es") == 1
    assert language_settings.rank("castellano", "only_castellano") == 0
    assert language_settings.rank("latino", "only_castellano", "es") is None


def test_castellano_first_orders_sources_and_drops_latino_dub(tmp_path):
    db = seed(tmp_path)
    language_settings.save(str(db), "castellano_first")
    sources = Resolver(db).sources("movie:tmdb:1")
    assert [row["audio"] for row in sources] == ["castellano", "unknown"]


def test_castellano_first_accepts_latino_original(tmp_path):
    db = seed(tmp_path, original_language="es")
    language_settings.save(str(db), "castellano_first")
    sources = Resolver(db).sources("movie:tmdb:1")
    assert [row["audio"] for row in sources] == ["castellano", "latino", "unknown"]


def test_only_castellano_filters_sources(tmp_path):
    db = seed(tmp_path)
    language_settings.save(str(db), "only_castellano")
    sources = Resolver(db).sources("movie:tmdb:1")
    assert [row["audio"] for row in sources] == ["castellano"]
    chosen = Resolver(db).resolve("movie:tmdb:1")
    assert chosen.source == "torrent"
    assert chosen.release_id == sources[0]["release_id"]


def test_only_castellano_does_not_publish_english_strm(tmp_path):
    db = seed(tmp_path, spanish_seeds=0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE releases SET health='dead' WHERE audio='castellano'")
    conn.commit()
    conn.close()
    language_settings.save(str(db), "only_castellano")
    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert result["written"] == 0
    assert not (out / "movies" / "Test (2020)").exists()
