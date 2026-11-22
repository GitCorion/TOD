import sqlite3
from pathlib import Path

from catalogo import language_settings
from catalogo.db import init_db
from catalogo.resolver import Resolver
from catalogo.strm import export_strm

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path, *, spanish_seeds=5, english_seeds=50):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
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
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_rank_values():
    assert language_settings.rank("castellano", "prefer_spanish") == 0
    assert language_settings.rank("unknown", "prefer_spanish") == 1
    assert language_settings.rank("vose", "prefer_spanish") == 2
    assert language_settings.rank("unknown", "only_spanish") is None
    assert language_settings.rank("latino", "only_spanish") == 0


def test_prefer_spanish_orders_sources(tmp_path):
    db = seed(tmp_path)
    language_settings.save(str(db), "prefer_spanish")
    sources = Resolver(db).sources("movie:tmdb:1")
    assert [row["audio"] for row in sources] == ["castellano", "unknown"]


def test_only_spanish_filters_sources(tmp_path):
    db = seed(tmp_path)
    language_settings.save(str(db), "only_spanish")
    sources = Resolver(db).sources("movie:tmdb:1")
    assert [row["audio"] for row in sources] == ["castellano"]
    chosen = Resolver(db).resolve("movie:tmdb:1")
    assert chosen.source == "torrent"
    assert chosen.release_id == sources[0]["release_id"]


def test_only_spanish_does_not_publish_english_strm(tmp_path):
    db = seed(tmp_path, spanish_seeds=0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE releases SET health='dead' WHERE audio='castellano'")
    conn.commit()
    conn.close()
    language_settings.save(str(db), "only_spanish")
    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert result["written"] == 0
    assert not (out / "movies" / "Test (2020)").exists()
