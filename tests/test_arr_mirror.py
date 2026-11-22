import sqlite3
from pathlib import Path

from catalogo.arr_mirror import (
    mirror_library,
    pending_publish_keys,
    pending_source_keys,
)
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeRadarr:
    def movies(self):
        return [
            {
                "tmdbId": 100,
                "title": "Pelicula",
                "year": 2020,
                "monitored": True,
                "hasFile": False,
                "runtime": 90,
                "releaseDate": "2020-01-01",
                "images": [
                    {
                        "coverType": "poster",
                        "remoteUrl": "https://image.tmdb.org/t/p/original/p.jpg",
                    }
                ],
            }
        ]


class FakeSonarr:
    def series(self):
        return [
            {
                "id": 7,
                "tvdbId": 555,
                "tmdbId": 200,
                "title": "Serie",
                "year": 2021,
                "monitored": True,
                "images": [
                    {
                        "coverType": "poster",
                        "remoteUrl": "https://image.tmdb.org/t/p/original/s.jpg",
                    }
                ],
            }
        ]

    def episodes(self, series_id):
        return [
            {
                "seasonNumber": 1,
                "episodeNumber": 2,
                "title": "Piloto",
                "airDate": "2021-01-02",
                "monitored": True,
                "hasFile": False,
            },
            {
                "seasonNumber": 0,
                "episodeNumber": 1,
                "title": "Especial",
                "monitored": True,
                "hasFile": False,
            },
        ]


def test_mirror_imports_arr_without_monitoring_in_tod(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    report = mirror_library(db, FakeRadarr(), FakeSonarr())
    assert report["radarr"]["items"] == 1
    assert report["sonarr"]["episodes"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    movie = conn.execute(
        "SELECT * FROM media_items WHERE key='movie:tmdb:100'"
    ).fetchone()
    assert movie["library_added"] == 1
    assert movie["library_source"] == "radarr"
    assert movie["monitored"] == 0
    assert movie["poster_path"].startswith("https://image.tmdb.org/")
    episode = conn.execute(
        "SELECT * FROM media_items WHERE key='series:tmdb:200:1:2'"
    ).fetchone()
    assert episode["library_added"] == 1
    assert episode["library_source"] == "sonarr"
    assert episode["tvdb_id"] == 555
    assert episode["monitored"] == 0
    conn.close()


def test_pending_source_keys_excludes_alive_and_local(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    mirror_library(db, FakeRadarr(), FakeSonarr())
    keys = pending_source_keys(db, limit=10)
    assert ("movie:tmdb:100", "movie") in keys
    assert ("series:tmdb:200:1:2", "series") in keys

    conn = sqlite3.connect(db)
    asset_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:100'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders,size_bytes) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "Tracker", "Pelicula.mkv", "alive", 5, 1000),
    )
    episode_id = conn.execute(
        "SELECT id FROM media_items WHERE key='series:tmdb:200:1:2'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,file_index,rel_path,state,"
        "storage_location,local_path) VALUES(?,?,?,?,?,?)",
        (episode_id, 0, "Serie.mkv", "ready", "LOCAL_DISK", "/tmp/Serie.mkv"),
    )
    conn.commit()
    conn.close()

    keys = pending_source_keys(db, limit=10)
    assert keys == []


def test_pending_publish_keys_finds_library_files(tmp_path):
    db = tmp_path / "catalogo.db"
    root = tmp_path / "catalogo"
    init_db(db, MIGRATIONS)
    mirror_library(db, FakeRadarr(), FakeSonarr())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,source) "
        "VALUES('movie',100,0,0,?,?, 'radarr')",
        (str(tmp_path / "Pelicula.mkv"), 1000),
    )
    conn.commit()
    conn.close()

    assert pending_publish_keys(db, root, limit=10) == ["movie:tmdb:100"]
