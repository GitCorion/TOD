import sqlite3
from pathlib import Path

from catalogo.admin import set_series_monitored
from catalogo.config import Config
from catalogo.db import init_db
from catalogo.intake import set_follow

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    rows = (
        ("series:tmdb:1:1:1", 1, 1, 1, 0),
        ("series:tmdb:1:1:2", 1, 1, 1, 0),
        ("series:tmdb:1:2:1", 2, 1, 1, 0),
        ("series:tmdb:1:2:2", 2, 1, 1, 1),
    )
    for key, season, episode, monitored, library_added in rows:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,season,episode,"
            "monitored,library_added) VALUES(?,?,?,?,?,?,?,?)",
            (key, "series", 1, "Serie", season, episode, monitored, library_added),
        )
    conn.commit()
    conn.close()
    return db


def config_for(tmp_path, db):
    return Config(
        db_path=db,
        legacy_db=db,
        library_root=tmp_path / "library",
        seed_root=tmp_path / "descargas",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:1/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1",
        qbittorrent_user="x",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:1/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=0,
    )


def monitored(db):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT key, monitored FROM media_items ORDER BY key"
    ).fetchall()
    conn.close()
    return dict(rows)


def test_unmonitor_series_respects_arr_mirror(tmp_path):
    db = seed(tmp_path)
    result = set_series_monitored(config_for(tmp_path, db), 1, monitored=False)
    assert result["titles"] == 3
    assert monitored(db) == {
        "series:tmdb:1:1:1": 0,
        "series:tmdb:1:1:2": 0,
        "series:tmdb:1:2:1": 0,
        "series:tmdb:1:2:2": 1,
    }


def test_monitor_one_season_only(tmp_path):
    db = seed(tmp_path)
    set_series_monitored(config_for(tmp_path, db), 1, monitored=False)
    result = set_series_monitored(
        config_for(tmp_path, db), 1, monitored=True, seasons=[2]
    )
    assert result["seasons"] == [2]
    assert result["titles"] == 1
    assert monitored(db) == {
        "series:tmdb:1:1:1": 0,
        "series:tmdb:1:1:2": 0,
        "series:tmdb:1:2:1": 1,
        "series:tmdb:1:2:2": 1,
    }


def test_set_follow_false_leaves_arr_items_alone(tmp_path):
    db = seed(tmp_path)
    changed = set_follow(
        db,
        ["series:tmdb:1:1:1", "series:tmdb:1:2:2"],
        follow=False,
    )
    assert changed == 1
    assert monitored(db)["series:tmdb:1:1:1"] == 0
    assert monitored(db)["series:tmdb:1:2:2"] == 1
