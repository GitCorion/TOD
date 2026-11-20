import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.abandon import abandon_idle
from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.stream_leases import touch

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class FakeAbandonClient:
    def __init__(self):
        self.calls = []

    def pause(self, info_hash):
        self.calls.append(("pause", info_hash))

    def delete(self, info_hash, *, delete_files=False):
        self.calls.append(("delete", info_hash, delete_files))


def seed(
    tmp_path,
    *,
    progress=0.02,
    privacy="public",
    action="delete",
    percent=5,
    with_file=True,
    added_at=None,
):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2025, "DOWNLOADING_LOCAL"),
    )
    media_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (media_id, "Tracker", "Test.mkv", 1000, "alive", "prowlarr", privacy),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,progress,"
        "qbit_state,save_path,base_path,added_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "hash1",
            release_id,
            "Tracker",
            "downloading",
            "ondemand",
            progress,
            "downloading",
            "/mnt/descargas/ondemand",
            "/mnt/descargas/ondemand",
            added_at or (NOW - timedelta(minutes=30)).isoformat(),
        ),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    if with_file:
        conn.execute(
            "INSERT INTO torrent_files(asset_id,torrent_id,file_index,rel_path,"
            "size_bytes,state,storage_location,local_path) VALUES(?,?,?,?,?,?,?,?)",
            (
                media_id,
                torrent_id,
                0,
                "Test.mkv",
                1000,
                "downloading",
                "LOCAL_DISK",
                "/mnt/descargas/ondemand/Test.mkv",
            ),
        )
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "Tracker",
            privacy=privacy,
            abandon_under_percent=percent,
            abandon_action=action,
            keep_for_bonus=privacy == "private",
        ),
    )
    return db


def test_public_delete_dry_run(tmp_path):
    db = seed(tmp_path)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=True, now=NOW)
    assert report["planned"][0]["action"] == "delete"
    assert report["planned"][0]["reason"] == "politica"
    assert client.calls == []


def test_public_delete_executes_and_purges(tmp_path):
    db = seed(tmp_path)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["deleted"][0]["info_hash"] == "hash1"
    assert client.calls == [("delete", "hash1", True)]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT state FROM torrents").fetchone()[0] == "removed"
    assert conn.execute(
        "SELECT state FROM torrent_files WHERE torrent_id=1"
    ).fetchone()[0] == "evicted"
    conn.close()


def test_private_delete_is_downgraded_to_pause(tmp_path):
    db = seed(tmp_path, privacy="private", action="delete")
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["paused"][0]["reason"] == "privado_h_r"
    assert client.calls == [("pause", "hash1")]


def test_private_delete_explicit_override(tmp_path):
    db = seed(tmp_path, privacy="private", action="delete")
    client = FakeAbandonClient()
    report = abandon_idle(
        str(db), client, dry_run=False, allow_private_delete=True, now=NOW
    )
    assert report["deleted"][0]["reason"] == "privado_borrado_explicito"
    assert client.calls == [("delete", "hash1", True)]


def test_active_stream_blocks_abandon(tmp_path):
    db = seed(tmp_path)
    touch(str(db), "movie:tmdb:1", "sess", now=NOW)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_streaming"] == ["movie:tmdb:1"]
    assert client.calls == []


def test_progress_above_threshold_is_kept(tmp_path):
    db = seed(tmp_path, progress=0.10)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_percent"] == 1
    assert client.calls == []


def test_disabled_policy_does_nothing(tmp_path):
    db = seed(tmp_path, percent=0)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_disabled"] == 1
    assert client.calls == []


def test_recent_torrent_is_skipped(tmp_path):
    db = seed(tmp_path, added_at=NOW.isoformat())
    client = FakeAbandonClient()
    report = abandon_idle(
        str(db), client, dry_run=False, min_age_seconds=600, now=NOW
    )
    assert report["skipped_recent"] == ["hash1"]
    assert client.calls == []


def test_unlinked_torrent_is_skipped(tmp_path):
    db = seed(tmp_path, with_file=False)
    client = FakeAbandonClient()
    report = abandon_idle(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_no_media"] == ["hash1"]
    assert client.calls == []
