import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.lookup import lookup_strm

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2024, "COLD"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2 (API)", "Test.mkv", 1000, "alive",
         "prowlarr", "private"),
    )
    conn.commit()
    conn.close()
    return db


def strm(tmp_path, content):
    path = tmp_path / "library" / "Test (2024)" / "Test.strm"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_lookup_strm_returns_catalog_status(tmp_path):
    db = seed(tmp_path)
    path = strm(
        tmp_path,
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1&release=1&download=0\n",
    )
    result = lookup_strm(
        str(db), str(path), allowed_roots=(str(tmp_path),)
    )
    assert result["is_catalog"] is True
    assert result["key"] == "movie:tmdb:1"
    assert result["release_id"] == 1
    assert "key=movie%3Atmdb%3A1" in result["status_url"]
    assert "release=1" in result["status_url"]
    assert "download=1" in result["play_url"]
    assert result["status"]["state"] == "COLD"


def test_lookup_rejects_non_strm_and_outside_root(tmp_path):
    db = seed(tmp_path)
    txt = tmp_path / "library" / "Test.mkv"
    txt.parent.mkdir(parents=True)
    txt.write_text("x")
    assert lookup_strm(
        str(db), str(txt), allowed_roots=(str(tmp_path),)
    )["is_catalog"] is False

    outside = tmp_path.parent / "outside.strm"
    outside.write_text("http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1")
    result = lookup_strm(
        str(db), str(outside), allowed_roots=(str(tmp_path),)
    )
    assert result["is_catalog"] is False
    assert result["reason"] == "path_no_permitido"


def test_lookup_rejects_strm_without_key(tmp_path):
    db = seed(tmp_path)
    path = strm(tmp_path, "http://127.0.0.1:8773/healthz\n")
    result = lookup_strm(
        str(db), str(path), allowed_roots=(str(tmp_path),)
    )
    assert result["is_catalog"] is False
    assert result["reason"] == "sin_key"
