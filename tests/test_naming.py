"""Tests de naming.py — Fase 3."""

import sqlite3
from pathlib import Path
from contextlib import closing

import pytest

from catalogo import naming


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS root_folders (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'any',
            path TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_items (
            id INTEGER PRIMARY KEY,
            key TEXT UNIQUE,
            kind TEXT,
            tmdb INTEGER,
            title TEXT,
            year INTEGER,
            season INTEGER,
            episode INTEGER,
            root_folder_id INTEGER,
            original_language TEXT,
            original_country TEXT,
            genres TEXT,
            series_type TEXT,
                is_anime INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_settings (
            tmdb INTEGER PRIMARY KEY,
            root_folder_id INTEGER
        )
    """)
    conn.commit()
    conn.close()
    return db


def test_safe_segment_cleans_invalid():
    assert naming.safe_segment('Hello: World?') == 'Hello World'
    assert naming.safe_segment(None) == 'Sin titulo'
    assert naming.safe_segment('') == 'Sin titulo'


def test_load_defaults(tmp_db):
    cfg = naming.load_naming_settings(tmp_db)
    assert cfg["movie_folder"] == "{Movie Title} ({Release Year})"
    assert cfg["use_season_folders"] is True
    assert cfg["separate_anime"] is False
    assert cfg["rich_nfo"] is True


def test_save_and_load(tmp_db):
    naming.save_naming_settings(tmp_db, {
        "movie_folder": "{Original Title} [{Year}]",
        "separate_anime": True,
    })
    cfg = naming.load_naming_settings(tmp_db)
    assert cfg["movie_folder"] == "{Original Title} [{Year}]"
    assert cfg["separate_anime"] is True


def test_build_movie_path_default():
    asset = {"kind": "movie", "title": "Interstellar", "year": 2014,
             "tmdb": 157336, "key": "movie:tmdb:157336"}
    p = naming.build_asset_strm_path(Path("/mnt/catalogo"), asset)
    assert str(p) == "/mnt/catalogo/movies/Interstellar (2014)/Interstellar (2014).strm"


def test_build_series_path_default():
    asset = {"kind": "series", "title": "Breaking Bad", "year": 2008,
             "tmdb": 1396, "season": 2, "episode": 5,
             "key": "series:tmdb:1396:2:5"}
    p = naming.build_asset_strm_path(Path("/mnt/catalogo"), asset)
    assert str(p) == "/mnt/catalogo/series/Breaking Bad (2008)/Season 02/Breaking Bad S02E05.strm"


def test_build_movie_path_with_cfg():
    cfg = {
        "movie_folder": "{Original Title} ({Release Year})",
        "movie_file": "{Movie Title}",
        "series_folder": "",
        "season_folder": "",
        "episode_file": "",
        "use_season_folders": True,
        "version_suffix": "",
        "separate_anime": False,
        "anime_series_folder": "anime",
        "anime_movies_folder": "movies",
        "rich_nfo": True,
    }
    asset = {"kind": "movie", "title": "Interstellar", "original_title": "Interstellar",
             "year": 2014, "tmdb": 157336, "key": "m:157336"}
    p = naming.build_asset_strm_path(Path("/mnt"), asset, cfg=cfg)
    assert str(p) == "/mnt/movies/Interstellar (2014)/Interstellar.strm"


def test_anime_separation():
    cfg = naming.DEFAULTS.copy()
    cfg_parsed = {
        "movie_folder": "{Movie Title} ({Release Year})",
        "movie_file": "{Movie Title} ({Release Year})",
        "series_folder": "{Series Title} ({Year})",
        "season_folder": "Season {season:00}",
        "episode_file": "{Series Title} S{season:00}E{episode:00}",
        "use_season_folders": True,
        "version_suffix": "",
        "separate_anime": True,
        "anime_series_folder": "anime",
        "anime_movies_folder": "movies",
        "rich_nfo": True,
    }
    anime = {"kind": "series", "title": "One Piece", "year": 1999,
             "tmdb": 37854, "season": 1, "episode": 1,
             "key": "s:37854:1:1", "is_anime": 1}
    p = naming.build_asset_strm_path(Path("/mnt/catalogo"), anime, cfg=cfg_parsed)
    assert "/anime/" in str(p)

    normal = {"kind": "series", "title": "Breaking Bad", "year": 2008,
              "tmdb": 1396, "season": 1, "episode": 1,
              "key": "s:1396:1:1"}
    p2 = naming.build_asset_strm_path(Path("/mnt/catalogo"), normal, cfg=cfg_parsed)
    assert "/series/" in str(p2)


def test_root_folder_override(tmp_db):
    rf = naming.save_root_folder(tmp_db, name="4K Movies", kind="movie",
                                 path="/mnt/4k/movies", is_default=False)
    asset = {"kind": "movie", "title": "Dune", "year": 2021,
             "tmdb": 438631, "key": "m:438631",
             "root_folder_id": rf["id"]}
    p = naming.build_asset_strm_path(
        Path("/mnt/catalogo"), asset,
        root_folders_by_id={rf["id"]: rf},
    )
    assert str(p).startswith("/mnt/4k/movies/")


def test_root_folder_crud(tmp_db):
    rf = naming.save_root_folder(tmp_db, name="Series", kind="series",
                                 path="/mnt/series", is_default=True)
    assert rf["name"] == "Series"
    assert rf["is_default"] is True

    folders = naming.list_root_folders(tmp_db)
    assert len(folders) >= 1

    naming.save_root_folder(tmp_db, name="Series Renamed", kind="series",
                            path="/mnt/series2", is_default=True, folder_id=rf["id"])
    folders = naming.list_root_folders(tmp_db)
    updated = next(f for f in folders if f["id"] == rf["id"])
    assert updated["name"] == "Series Renamed"

    # Cannot delete default
    with pytest.raises(ValueError, match="por defecto"):
        naming.delete_root_folder(tmp_db, rf["id"])


def test_preview_examples(tmp_db):
    cfg = naming.load_naming_settings(tmp_db)
    examples = naming.preview_examples(cfg)
    assert "movie" in examples
    assert "series" in examples
    assert "anime" in examples
    assert "Interstellar" in examples["movie"]
    assert "Juego de Tronos" in examples["series"]


def test_plan_reorganize_empty(tmp_db, tmp_path):
    moves = naming.plan_reorganize(tmp_db, tmp_path)
    assert moves == []


def test_is_anime():
    assert naming._is_anime_asset({"is_anime": 1})
    assert naming._is_anime_asset({"series_type": "anime"})
    assert naming._is_anime_asset({
        "original_language": "ja", "original_country": "JP",
        "genres": "Animation, Action",
    })
    assert not naming._is_anime_asset({
        "original_language": "en", "original_country": "US",
        "genres": "Drama",
    })


def test_apply_reorganize_moves_and_cleans(tmp_db, tmp_path):
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO media_items(key, kind, tmdb, title, year, season, episode, is_anime) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        ("series:tmdb:37854:1:1", "series", 37854, "One Piece", 1999, 1, 1, 1),
    )
    conn.commit()
    conn.close()

    old_dir = tmp_path / "series" / "One Piece (1999)" / "Season 01"
    old_dir.mkdir(parents=True)
    old_strm = old_dir / "One Piece S01E01.strm"
    old_strm.write_text("http://127.0.0.1:8773/play?key=series:tmdb:37854:1:1\n", encoding="utf-8")

    # Activar separación de anime
    naming.save_naming_settings(tmp_db, {"separate_anime": True})
    res = naming.apply_reorganize(tmp_db, tmp_path)
    assert res["planned"] == 1
    assert res["moved"] == 1
    assert res["errors"] == []
    expected = tmp_path / "anime" / "One Piece (1999)" / "Season 01" / "One Piece S01E01.strm"
    assert expected.is_file()
    assert not old_strm.exists()
    assert not (tmp_path / "series" / "One Piece (1999)").exists()
