import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.qbit_sync import sync

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class MovingQbit:
    def __init__(self, state="moving", save_path="/mnt/fichier/descargas"):
        self.state = state
        self.save_path = save_path
        self.files_calls = []

    def torrents(self, category=None):
        return [
            {
                "hash": "mov1", "category": "completo", "state": self.state,
                "progress": 1, "save_path": self.save_path,
                "content_path": "/mnt/descargas/radarr/Move.mkv",
                "download_path": "/mnt/cache/tmp/completo",
                "completion_on": 1794981060, "seeding_time": 100,
                "added_on": 1794980000, "uploaded": 0, "downloaded": 5000,
                "ratio": 0, "size": 5000, "tracker": "TrackerEspanol2",
            }
        ]

    def files(self, info_hash):
        self.files_calls.append(info_hash)
        return [{"index": 0, "name": "Move.mkv", "size": 5000,
                 "progress": 1, "priority": 1}]


def make_radarr(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE MovieMetadata (Id INTEGER PRIMARY KEY, TmdbId INTEGER,
                                    Title TEXT, Year INTEGER);
        CREATE TABLE Movies (Id INTEGER PRIMARY KEY, MovieMetadataId INTEGER);
        CREATE TABLE History (Id INTEGER PRIMARY KEY, DownloadId TEXT,
                              MovieId INTEGER);
        INSERT INTO MovieMetadata VALUES (1, 300, 'Moving Movie', 2026);
        INSERT INTO Movies VALUES (1, 1);
        INSERT INTO History VALUES (1, 'MOV1', 1);
        """
    )
    conn.commit()
    conn.close()


def test_moving_is_not_missing_and_transitions_to_1fichier(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    radarr = tmp_path / "radarr.db"
    make_radarr(radarr)
    client = MovingQbit()

    report = sync(
        db, client, radarr_db=radarr, sonarr_db=None,
        check_exists=False,
    )
    assert report.missing_files == 0
    assert report.errors == []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    media = conn.execute(
        "SELECT * FROM media_items WHERE key='movie:tmdb:300'"
    ).fetchone()
    assert media["status"] == "MOVING_TO_1FICHIER"
    file_row = conn.execute(
        "SELECT * FROM torrent_files WHERE torrent_id=("
        "SELECT id FROM torrents WHERE info_hash='mov1')"
    ).fetchone()
    assert file_row["state"] == "ready"
    assert file_row["storage_location"] == "LOCAL_DISK"
    assert file_row["local_path"].endswith("Move.mkv")
    assert file_row["cloud_uploaded_at"] is None
    conn.close()

    # Simula que el move termina: qBittorrent ya ve el fichero en 1fichier.
    client.state = "stalledUP"
    report = sync(
        db, client, radarr_db=radarr, sonarr_db=None,
        check_exists=False,
    )
    assert report.missing_files == 0
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    media = conn.execute(
        "SELECT * FROM media_items WHERE key='movie:tmdb:300'"
    ).fetchone()
    assert media["status"] == "ON_1FICHIER"
    file_row = conn.execute(
        "SELECT * FROM torrent_files WHERE torrent_id=("
        "SELECT id FROM torrents WHERE info_hash='mov1')"
    ).fetchone()
    assert file_row["storage_location"] == "1FICHIER"
    assert file_row["cloud_uploaded_at"] is not None
    conn.close()
