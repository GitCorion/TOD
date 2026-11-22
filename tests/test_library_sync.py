import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.library_sync import sync_library

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeRadarr:
    def __init__(self, root):
        self.root = root

    def movies(self):
        return [
            {
                "id": 1,
                "tmdbId": 100,
                "hasFile": True,
                "path": f"{self.root}/Peli (2024)",
                "movieFile": {
                    "relativePath": "Peli (2024) WEBDL-1080p.mkv",
                    "size": 123,
                },
            }
        ]


class FakeSonarr:
    def __init__(self, root):
        self.root = root

    def series(self):
        return [
            {
                "id": 1,
                "tmdbId": 200,
                "path": f"{self.root}/Serie (2024)",
            }
        ]

    def episodes(self, series_id):
        return [
            {
                "seasonNumber": 1,
                "episodeNumber": 2,
                "hasFile": True,
                "episodeFileId": 1,
            }
        ]

    def episode_files(self, series_id):
        return [
            {
                "id": 1,
                "path": (
                    f"{self.root}/Serie (2024)/Season 1/"
                    "Serie - S01E02 - Piloto.mkv"
                ),
                "size": 456,
            }
        ]


def test_sync_library_rebuilds_paths(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    radarr = FakeRadarr("/mnt/library/Peliculas")
    sonarr = FakeSonarr("/mnt/library/Series")

    report = sync_library(db, radarr=radarr, sonarr=sonarr)
    assert report["ok"] is True
    assert report["movies"] == 1
    assert report["episodes"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        (row["kind"], row["tmdb"], row["season"], row["episode"]): row
        for row in conn.execute("SELECT * FROM library_files")
    }
    assert rows[("movie", 100, 0, 0)]["path"] == (
        "/mnt/library/Peliculas/Peli (2024)/Peli (2024) WEBDL-1080p.mkv"
    )
    assert rows[("series", 200, 1, 2)]["path"] == (
        "/mnt/library/Series/Serie (2024)/Season 1/"
        "Serie - S01E02 - Piloto.mkv"
    )
    conn.close()


def test_sync_library_keeps_known_paths_when_api_fails(tmp_path):
    class Broken:
        def movies(self):
            raise RuntimeError("radarr caído")

        def series(self):
            raise RuntimeError("sonarr caído")

        def episodes(self, series_id):
            raise RuntimeError("sonarr caído")

        def episode_files(self, series_id):
            raise RuntimeError("sonarr caído")

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    ok = sync_library(
        db,
        radarr=FakeRadarr("/mnt/library/Peliculas"),
        sonarr=FakeSonarr("/mnt/library/Series"),
    )
    assert ok["ok"] is True

    failed = sync_library(db, radarr=Broken(), sonarr=Broken())
    assert failed["ok"] is False
    assert failed["errors"]

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM library_files").fetchone()[0] == 2
    conn.close()


def test_sync_library_updates_only_the_source_that_works(tmp_path):
    class BrokenRadarr:
        def movies(self):
            raise RuntimeError("radarr caído")

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    sync_library(
        db,
        radarr=FakeRadarr("/mnt/library/Peliculas"),
        sonarr=FakeSonarr("/mnt/library/Series"),
    )

    moved = FakeSonarr("/mnt/nuevo/Series")
    report = sync_library(db, radarr=BrokenRadarr(), sonarr=moved)
    assert report["ok"] is True
    assert report["errors"]

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        row["source"]: row
        for row in conn.execute("SELECT * FROM library_files")
    }
    assert rows["radarr"]["path"].startswith("/mnt/library/Peliculas")
    assert rows["sonarr"]["path"].startswith("/mnt/nuevo/Series")
    conn.close()
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    radarr = FakeRadarr("/mnt/library/Peliculas")
    sonarr = FakeSonarr("/mnt/library/Series")

    report = sync_library(db, radarr=radarr, sonarr=sonarr)
    assert report["ok"] is True
    assert report["movies"] == 1
    assert report["episodes"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        (row["kind"], row["tmdb"], row["season"], row["episode"]): row
        for row in conn.execute("SELECT * FROM library_files")
    }
    assert rows[("movie", 100, 0, 0)]["path"] == (
        "/mnt/library/Peliculas/Peli (2024)/Peli (2024) WEBDL-1080p.mkv"
    )
    assert rows[("series", 200, 1, 2)]["path"] == (
        "/mnt/library/Series/Serie (2024)/Season 1/"
        "Serie - S01E02 - Piloto.mkv"
    )
    conn.close()
