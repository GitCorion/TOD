import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.qbit_sync import sync

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class MovingQbit:
    def __init__(self, state="moving", save_path="/mnt/library/archive"):
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


class FakeRadarr:
    def movies(self):
        return [
            {
                "id": 1,
                "tmdbId": 300,
                "title": "Moving Movie",
                "year": 2026,
            }
        ]

    def history_all(self, **_kwargs):
        return [{"downloadId": "MOV1", "movieId": 1}]


def test_moving_is_not_missing_and_transitions_to_archive(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    radarr = FakeRadarr()
    client = MovingQbit()

    report = sync(
        db, client, radarr=radarr,
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

    # Simula que el move termina: qBittorrent ya ve el fichero en archivo.
    client.state = "stalledUP"
    report = sync(
        db, client, radarr=radarr,
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
