import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.prowlarr_sync import sync_private

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeProwlarr:
    def __init__(self):
        self.calls = []

    def search(self, query, *, media_type="movie", indexer_ids=None, limit=50):
        self.calls.append((query, media_type, indexer_ids))
        return [{
            "title": "Dune (2021) WEBDL-1080p AC3 5.1 x265.mkv Spanish",
            "size": 4098056172,
            "seeders": 30,
            "peers": 2,
            "indexerId": 21,
            "indexer": "TrackerEspanol3 (API)",
            "guid": "https://tracker/download/1",
            "downloadUrl": "http://127.0.0.1:9696/21/download?apikey=x&link=y",
            "tmdbId": 438631,
        }]


def test_private_sync_inserts_without_download(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:438631", "movie:tmdb:438631", "movie", 438631, "Dune 2021", 2021, "COLD"),
    )
    conn.commit()
    conn.close()
    client = FakeProwlarr()
    report = sync_private(
        str(db), client, tracker="TrackerEspanol3 (API)", indexer_id=21,
        key="movie:tmdb:438631", limit=1, delay=0,
    )
    assert report["inserted"] == 1
    assert report["errors"] == []
    assert client.calls[0][2] == [21]

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM releases").fetchone()
    assert row["tracker"] == "TrackerEspanol3 (API)"
    assert row["privacy"] == "private"
    assert row["source"] == "prowlarr"
    assert row["health"] == "alive"
    assert row["seeders"] == 30
    assert row["debrid_link"] is None
    conn.close()
