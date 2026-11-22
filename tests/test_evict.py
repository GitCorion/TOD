import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.evict import evict_completed

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


class FakeQbit:
    def __init__(self):
        self.deleted = []

    def delete(self, info_hash, delete_files=True):
        self.deleted.append((info_hash, delete_files))


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    media = tmp_path / "media" / "Test.mkv"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"x" * 100)
    start = (NOW - timedelta(hours=200)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tracker_policies(tracker,privacy,min_seed_hours,"
        "target_ratio,enabled) VALUES(?,?,?,?,?)",
        ("TrackerEspanol2", "private", 120, 0.0, 1),
    )
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020, "SEEDING_LOCAL"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 100, "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "qbit_state,progress,save_path) VALUES(?,?,?,?,?,?,?,?)",
        ("h1", release_id, "TrackerEspanol2", "seeding", "ondemand",
         "forcedUP", 1.0, str(tmp_path / "media")),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "Test.mkv", str(media), 100,
         "ready"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state,seed_required_until,started_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", "h1", 100, "seeding",
         (NOW - timedelta(hours=80)).isoformat(), start),
    )
    conn.commit()
    conn.close()
    return db, asset_id, release_id, media


def test_evict_deletes_catalog_copy_after_seed(tmp_path):
    db, asset_id, release_id, media = seed(tmp_path)
    client = FakeQbit()
    report = evict_completed(
        db, client, limit=5, dry_run=False,
        roots=(str(tmp_path),), now=NOW,
    )
    assert report["evicted"], report
    assert client.deleted == [("h1", True)]
    assert not media.exists()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE release_id=?",
        (release_id,),
    ).fetchone()["state"] == "done"
    assert conn.execute(
        "SELECT state, qbit_state FROM torrents WHERE info_hash='h1'"
    ).fetchone()[:] == ("removed", "removed")
    assert conn.execute(
        "SELECT state, local_path FROM torrent_files WHERE asset_id=?",
        (asset_id,),
    ).fetchone()[:] == ("evicted", None)
    assert conn.execute(
        "SELECT status FROM media_items WHERE id=?", (asset_id,)
    ).fetchone()["status"] == "COLD"
    conn.close()


def test_evict_dry_run_does_not_delete(tmp_path):
    db, _asset_id, _release_id, media = seed(tmp_path)
    client = FakeQbit()
    report = evict_completed(
        db, client, limit=5, dry_run=True,
        roots=(str(tmp_path),), now=NOW,
    )
    assert report["planned"]
    assert client.deleted == []
    assert media.exists()


def test_evict_skips_active_stream(tmp_path):
    db, asset_id, _release_id, media = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO stream_leases(media_id,session_id,last_seen) "
        "VALUES(?,?,?)",
        ("movie:tmdb:1", "s1", NOW.isoformat()),
    )
    conn.commit()
    conn.close()
    client = FakeQbit()
    report = evict_completed(
        db, client, limit=5, dry_run=False,
        roots=(str(tmp_path),), now=NOW,
    )
    assert report["skipped_streaming"] == ["movie:tmdb:1"]
    assert client.deleted == []
    assert media.exists()


def test_evict_skips_shared_pack(tmp_path):
    db, asset_id, release_id, media = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
        "VALUES(?,?,?,?,?,?,?)",
        ("series:tmdb:2:1:2", "series", 2, "Pack", 2026, 1, 2),
    )
    other = conn.execute(
        "SELECT id FROM media_items WHERE key='series:tmdb:2:1:2'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,is_pack) "
        "VALUES(?,?,?,?,?,?)",
        (other, "TrackerEspanol2", "Pack S01E02", 100, "alive", 1),
    )
    other_release = conn.execute(
        "SELECT id FROM releases ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    torrent_id = conn.execute(
        "SELECT id FROM torrents WHERE info_hash='h1'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (other, other_release, torrent_id, 1, "Pack S01E02.mkv",
         str(media), 100, "ready"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state,started_at) VALUES(?,?,?,?,?,?,?)",
        (other, other_release, "TrackerEspanol2", None, 100, "seeding",
         NOW.isoformat()),
    )
    conn.commit()
    conn.close()
    client = FakeQbit()
    report = evict_completed(
        db, client, limit=5, dry_run=False,
        roots=(str(tmp_path),), now=NOW,
    )
    assert report["skipped_pack"] == ["movie:tmdb:1"]
    assert client.deleted == []
    assert media.exists()
    assert asset_id != other
