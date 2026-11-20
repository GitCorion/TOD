import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.qbit_sync import sync

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class PackQbit:
    def __init__(self):
        self.files_calls = []

    def torrents(self, category=None):
        return [
            {
                "hash": "ser1", "category": "completo", "state": "stalledUP",
                "progress": 1, "save_path": "/mnt/fichier/descargas",
                "content_path": "/mnt/fichier/descargas/Test Show S01",
                "download_path": "/mnt/cache/tmp/completo",
                "completion_on": 1794000000, "seeding_time": 100,
                "added_on": 1794000000, "uploaded": 0, "downloaded": 3000,
                "ratio": 0, "size": 3000, "tracker": "TrackerEspanol2",
            }
        ]

    def files(self, info_hash):
        self.files_calls.append(info_hash)
        return [
            {"index": 0, "name": "Test Show/Season 01/Test Show S01E01.mkv",
             "size": 1000, "progress": 1, "priority": 1},
            {"index": 1, "name": "Test Show/Season 01/Test Show S01E02.mkv",
             "size": 2000, "progress": 1, "priority": 1},
            {"index": 2, "name": "Test Show/readme.txt",
             "size": 10, "progress": 1, "priority": 0},
        ]


def make_sonarr_pack(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE Series (Id INTEGER PRIMARY KEY, TvdbId INTEGER,
                             TmdbId INTEGER, Title TEXT);
        CREATE TABLE Episodes (Id INTEGER PRIMARY KEY, SeasonNumber INTEGER,
                               EpisodeNumber INTEGER);
        CREATE TABLE History (Id INTEGER PRIMARY KEY, DownloadId TEXT,
                              SeriesId INTEGER, EpisodeId INTEGER);
        INSERT INTO Series VALUES (1, 555, 200, 'Test Show');
        INSERT INTO Episodes VALUES (1, 1, 1);
        INSERT INTO Episodes VALUES (2, 1, 2);
        INSERT INTO History VALUES (1, 'SER1', 1, 1);
        INSERT INTO History VALUES (2, 'SER1', 1, 2);
        """
    )
    conn.commit()
    conn.close()


def test_pack_maps_each_episode_and_keeps_readme_unassigned(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    sonarr = tmp_path / "sonarr.db"
    make_sonarr_pack(sonarr)
    client = PackQbit()

    report = sync(
        db, client, radarr_db=None, sonarr_db=sonarr,
        check_exists=False, now=None,
    )
    assert report.errors == []
    assert client.files_calls == ["ser1"]

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    keys = {
        row["key"]: row for row in conn.execute(
            "SELECT key, status, tvdb_id FROM media_items "
            "WHERE key LIKE 'series:%'"
        )
    }
    assert "series:tmdb:200:1:1" in keys
    assert "series:tmdb:200:1:2" in keys
    assert keys["series:tmdb:200:1:1"]["tvdb_id"] == 555
    assert keys["series:tmdb:200:1:1"]["status"] == "ON_1FICHIER"

    files = conn.execute(
        "SELECT file_index, asset_id, storage_location, rel_path "
        "FROM torrent_files ORDER BY file_index"
    ).fetchall()
    assert len(files) == 3
    assert files[0]["asset_id"] is not None
    assert files[1]["asset_id"] is not None
    assert files[2]["asset_id"] is None
    assert all(row["storage_location"] == "1FICHIER" for row in files)
    conn.close()
