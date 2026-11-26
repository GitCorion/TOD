import sqlite3
from pathlib import Path

from catalogo.arr_mirror import (
    mirror_library,
    pending_publish_keys,
    pending_source_keys,
)
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


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


def test_removed_from_arr_returns_to_monitoring(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,library_added,"
        "library_source) VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:999", "movie", 999, "Ya no está", 2019, 1, "radarr"),
    )
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,season,episode,title,"
        "library_added,library_source) VALUES(?,?,?,?,?,?,?,?)",
        ("series:tmdb:888:1:1", "series", 888, 1, 1, "Serie ida", 1, "sonarr"),
    )
    conn.commit()
    conn.close()

    mirror_library(db, FakeRadarr(), FakeSonarr())

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    movie = conn.execute(
        "SELECT * FROM media_items WHERE key='movie:tmdb:999'"
    ).fetchone()
    episode = conn.execute(
        "SELECT * FROM media_items WHERE key='series:tmdb:888:1:1'"
    ).fetchone()
    assert movie["library_added"] == 0
    assert movie["monitored"] == 1
    assert movie["monitor_source"] == "auto"
    assert episode["library_added"] == 0
    assert episode["monitored"] == 1
    assert episode["monitor_source"] == "auto"
    conn.close()


def test_removed_episode_from_seen_series_returns_to_monitoring(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,season,episode,title,"
        "library_added,library_source) VALUES(?,?,?,?,?,?,?,?)",
        ("series:tmdb:200:1:9", "series", 200, 1, 9, "Episodio ido", 1, "sonarr"),
    )
    conn.commit()
    conn.close()

    mirror_library(db, FakeRadarr(), FakeSonarr())

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    episode = conn.execute(
        "SELECT * FROM media_items WHERE key='series:tmdb:200:1:9'"
    ).fetchone()
    assert episode["library_added"] == 0
    assert episode["monitored"] == 1
    assert episode["monitor_source"] == "auto"
    conn.close()


def test_arr_import_clears_auto_monitoring(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,monitored,"
        "monitor_source) VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:100", "movie", 100, "Pelicula", 2020, 1, "rss"),
    )
    conn.commit()
    conn.close()

    mirror_library(db, FakeRadarr(), FakeSonarr())

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT monitored FROM media_items WHERE key='movie:tmdb:100'"
    ).fetchone()[0] == 0
    conn.close()


def test_arr_mirror_does_not_unmonitor_bulk_marking(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,monitored,monitor_source) "
        "VALUES('movie:tmdb:100','movie',100,'Pelicula',1,'bulk')"
    )
    conn.commit()
    conn.close()

    mirror_library(db, FakeRadarr(), None)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT monitored, monitor_source FROM media_items "
        "WHERE key='movie:tmdb:100'"
    ).fetchone()
    conn.close()
    assert row == (1, "bulk")


class FallbackRadarr:
    def movies(self):
        return [
            {"id": 1, "tmdbId": 101, "title": "Sin fichero", "year": 2020,
             "monitored": True, "hasFile": False},
            {"id": 2, "tmdbId": 102, "title": "En cola", "year": 2020,
             "monitored": True, "hasFile": False},
            {"id": 3, "tmdbId": 103, "title": "Con fichero", "year": 2020,
             "monitored": True, "hasFile": True},
        ]

    def queue(self):
        return {"records": [{"movieId": 2, "downloadId": "abc"}]}


def test_arr_fallback_monitors_only_missing_and_not_queued(tmp_path):
    from catalogo.search_settings import save as save_search

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    save_search(str(db), arr_fallback=True)
    report = mirror_library(db, FallbackRadarr(), None)
    assert report["radarr"]["fallback"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        row["key"]: row for row in conn.execute(
            "SELECT key, monitored, monitor_source FROM media_items"
        )
    }
    conn.close()
    assert rows["movie:tmdb:101"]["monitored"] == 1
    assert rows["movie:tmdb:101"]["monitor_source"] == "arr_fallback"
    assert rows["movie:tmdb:102"]["monitored"] == 0
    assert rows["movie:tmdb:103"]["monitored"] == 0


def test_arr_fallback_respects_setting(tmp_path):
    from catalogo.search_settings import save as save_search

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    save_search(str(db), arr_fallback=False)
    mirror_library(db, FallbackRadarr(), None)
    conn = sqlite3.connect(db)
    values = [row[0] for row in conn.execute(
        "SELECT monitored FROM media_items"
    )]
    conn.close()
    assert values == [0, 0, 0]


class FallbackSonarr(FakeSonarr):
    def episodes(self, series_id):
        return [
            {"id": 1, "seasonNumber": 1, "episodeNumber": 1,
             "title": "Sin fichero", "monitored": True, "hasFile": False},
            {"id": 2, "seasonNumber": 1, "episodeNumber": 2,
             "title": "En cola", "monitored": True, "hasFile": False},
            {"id": 3, "seasonNumber": 1, "episodeNumber": 3,
             "title": "Con fichero", "monitored": True, "hasFile": True},
        ]

    def queue(self):
        return {"records": [{"episodeId": 2, "downloadId": "abc"}]}


def test_sonarr_fallback_monitors_missing_episodes(tmp_path):
    from catalogo.search_settings import save as save_search

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    save_search(str(db), arr_fallback=True)
    report = mirror_library(db, None, FallbackSonarr())
    assert report["sonarr"]["fallback"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        row["key"]: row for row in conn.execute(
            "SELECT key, monitored, monitor_source FROM media_items"
        )
    }
    conn.close()
    assert rows["series:tmdb:200:1:1"]["monitored"] == 1
    assert rows["series:tmdb:200:1:1"]["monitor_source"] == "arr_fallback"
    assert rows["series:tmdb:200:1:2"]["monitored"] == 0
    assert rows["series:tmdb:200:1:3"]["monitored"] == 0


class FallbackRadarrWithFile(FallbackRadarr):
    def movies(self):
        rows = super().movies()
        rows[0]["hasFile"] = True
        return rows


def test_fallback_stops_when_arr_gets_the_file(tmp_path):
    from catalogo.search_settings import save as save_search

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    save_search(str(db), arr_fallback=True)
    mirror_library(db, FallbackRadarr(), None)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT monitored FROM media_items WHERE key='movie:tmdb:101'"
    ).fetchone()[0] == 1
    conn.close()

    mirror_library(db, FallbackRadarrWithFile(), None)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT monitored FROM media_items WHERE key='movie:tmdb:101'"
    ).fetchone()[0] == 0
    conn.close()
