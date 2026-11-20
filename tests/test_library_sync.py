import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.library_sync import sync_library

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


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
