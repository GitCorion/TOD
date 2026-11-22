import sqlite3
from pathlib import Path

from catalogo.bridge import bridge_downloads
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeRadarr:
    def __init__(self, movie):
        self.movie = movie
        self.rescanned = []

    def movie_by_tmdb(self, tmdb):
        return self.movie

    def rescan_movie(self, movie_id):
        self.rescanned.append(movie_id)


class FakeSonarr:
    def __init__(self, series, episode):
        self.series = series
        self.episode_row = episode
        self.rescanned = []

    def series_by_tmdb(self, tmdb):
        return self.series

    def episode(self, series_id, season, episode):
        return self.episode_row

    def rescan_series(self, series_id):
        self.rescanned.append(series_id)


def seed(tmp_path, *, kind="movie"):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    conn = sqlite3.connect(db)
    if kind == "movie":
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year) "
            "VALUES(?,?,?,?,?)",
            ("movie:tmdb:1", "movie", 1, "Test Movie", 2020),
        )
    else:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
            "VALUES(?,?,?,?,?,?,?)",
            ("series:tmdb:2:1:3", "series", 2, "Serie", 2021, 1, 3),
        )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Source.mkv", 5, "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress) VALUES(?,?,?,?,?,?)",
        ("bridgehash", release_id, "TrackerEspanol2", "seeding", "ondemand", 1),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "source.mkv", str(source),
         5, "ready"),
    )
    conn.commit()
    conn.close()
    return db, source


def test_bridge_copies_movie_and_rescans(tmp_path):
    db, _source = seed(tmp_path, kind="movie")
    radarr = FakeRadarr(
        {
            "id": 7,
            "title": "Test Movie",
            "year": 2020,
            "path": str(tmp_path / "radarr" / "Test Movie (2020)"),
            "hasFile": False,
        }
    )
    report = bridge_downloads(
        db, radarr, None, limit=5, dry_run=False
    )
    assert report["bridged"], report
    destination = (
        tmp_path / "radarr" / "Test Movie (2020)" / "Test Movie (2020).mkv"
    )
    assert destination.is_file()
    assert destination.read_bytes() == b"video"
    assert radarr.rescanned == [7]


class FakeRadarrAdd:
    def __init__(self, root):
        self.root = root
        self.movie = None
        self.added = None
        self.deleted = []
        self.rescanned = []

    def movie_by_tmdb(self, tmdb):
        return self.movie

    def root_folders(self):
        return [{"path": str(self.root), "accessible": True}]

    def quality_profiles(self):
        return [{"id": 3, "name": "HD"}]

    def add_movie(self, **kwargs):
        self.added = kwargs
        self.movie = {
            "id": 8,
            "title": kwargs["title"],
            "year": kwargs["year"],
            "path": str(self.root / "Test Movie (2020)"),
            "hasFile": False,
        }
        return self.movie

    def rescan_movie(self, movie_id):
        self.rescanned.append(movie_id)

    def queue(self):
        return {"records": [{"id": 11, "movieId": 8}]}

    def delete_queue_item(self, queue_id, **_kwargs):
        self.deleted.append(queue_id)


def test_bridge_adds_missing_movie_and_clears_queue(tmp_path):
    db, _source = seed(tmp_path, kind="movie")
    radarr = FakeRadarrAdd(tmp_path / "radarr")
    report = bridge_downloads(db, radarr, None, limit=5, dry_run=False)
    assert report["bridged"], report
    assert radarr.added["tmdb"] == 1
    assert radarr.added["monitored"] is False
    destination = (
        tmp_path / "radarr" / "Test Movie (2020)" / "Test Movie (2020).mkv"
    )
    assert destination.read_bytes() == b"video"
    assert radarr.deleted == [11]


def test_bridge_skips_if_already_in_radarr(tmp_path):
    db, _source = seed(tmp_path, kind="movie")
    radarr = FakeRadarr(
        {
            "id": 7,
            "title": "Test Movie",
            "year": 2020,
            "path": str(tmp_path / "radarr" / "Test Movie (2020)"),
            "hasFile": True,
        }
    )
    report = bridge_downloads(db, radarr, None, limit=5, dry_run=False)
    assert report["bridged"] == []
    assert report["skipped"][0]["reason"] == "ya_en_radarr"
    assert radarr.rescanned == []


def test_bridge_copies_episode_and_rescans_series(tmp_path):
    db, _source = seed(tmp_path, kind="series")
    sonarr = FakeSonarr(
        {
            "id": 9,
            "title": "Serie",
            "path": str(tmp_path / "sonarr" / "Serie"),
        },
        {
            "seasonNumber": 1,
            "episodeNumber": 3,
            "title": "Piloto",
            "hasFile": False,
        },
    )
    report = bridge_downloads(db, None, sonarr, limit=5, dry_run=False)
    assert report["bridged"], report
    destination = (
        tmp_path / "sonarr" / "Serie" / "Season 1"
        / "Serie - S01E03 - Piloto.mkv"
    )
    assert destination.is_file()
    assert sonarr.rescanned == [9]


class RadarrByTmdb:
    def __init__(self, movies):
        self.movies = movies
        self.rescanned = []

    def movie_by_tmdb(self, tmdb):
        return self.movies.get(int(tmdb))

    def rescan_movie(self, movie_id):
        self.rescanned.append(movie_id)


def test_bridge_skips_existing_and_bridges_next_with_limit(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    sources = {}
    for tmdb, title in ((1, "Uno"), (2, "Dos")):
        source = tmp_path / f"{title}.mkv"
        source.write_bytes(b"video")
        sources[tmdb] = source
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year) "
            "VALUES(?,?,?,?,?)",
            (f"movie:tmdb:{tmdb}", "movie", tmdb, title, 2020),
        )
        asset_id = conn.execute(
            "SELECT id FROM media_items WHERE tmdb=?", (tmdb,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
            "VALUES(?,?,?,?,?)",
            (asset_id, "TrackerEspanol2", title, 5, "alive"),
        )
        release_id = conn.execute(
            "SELECT id FROM releases ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO torrents(info_hash,release_id,tracker,state,"
            "category,progress) VALUES(?,?,?,?,?,?)",
            (f"hash{tmdb}", release_id, "TrackerEspanol2", "seeding",
             "ondemand", 1),
        )
        torrent_id = conn.execute(
            "SELECT id FROM torrents ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO torrent_files(asset_id,release_id,torrent_id,"
            "file_index,rel_path,local_path,size_bytes,state) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (asset_id, release_id, torrent_id, 0, source.name,
             str(source), 5, "ready"),
        )
    conn.commit()
    conn.close()
    radarr = RadarrByTmdb(
        {
            1: {"id": 1, "title": "Uno", "year": 2020,
                "path": str(tmp_path / "radarr" / "Uno (2020)"),
                "hasFile": True},
            2: {"id": 2, "title": "Dos", "year": 2020,
                "path": str(tmp_path / "radarr" / "Dos (2020)"),
                "hasFile": False},
        }
    )
    report = bridge_downloads(db, radarr, None, limit=1, dry_run=False)
    assert [item["key"] for item in report["bridged"]] == ["movie:tmdb:2"]
    assert (tmp_path / "radarr" / "Dos (2020)" / "Dos (2020).mkv").is_file()
    assert radarr.rescanned == [2]
