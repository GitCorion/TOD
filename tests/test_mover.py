import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.mover import move_completed
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.stream_leases import active, cleanup, touch

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class FakeMoveClient:
    def __init__(self):
        self.calls = []

    def set_location(self, info_hash, location):
        self.calls.append((info_hash, location))


def seed(tmp_path, *, privacy="private", with_file=True):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2025, "SEEDING_LOCAL"),
    )
    media_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (media_id, "TrackerEspanol (API)", "Test.mkv", 1000, "alive", "prowlarr", privacy),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,progress,"
        "qbit_state,save_path,base_path) VALUES(?,?,?,?,?,?,?,?,?)",
        ("hash1", release_id, "TrackerEspanol (API)", "seeding", "ondemand", 1,
         "stalledUP", "/mnt/descargas/ondemand", "/mnt/descargas/ondemand"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    if with_file:
        conn.execute(
            "INSERT INTO torrent_files(asset_id,torrent_id,file_index,rel_path,"
            "size_bytes,state,storage_location) VALUES(?,?,?,?,?,?,?)",
            (media_id, torrent_id, 0, "Test.mkv", 1000, "ready", "LOCAL_DISK"),
        )
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol (API)", min_seed_hours=120, target_ratio=0,
            max_active=2, allow_partial=False, keep_for_bonus=True,
            privacy=privacy,
        ),
    )
    return db, media_id, "hash1"


def test_move_planned_and_executed_when_idle(tmp_path):
    db, media_id, hash_ = seed(tmp_path)
    client = FakeMoveClient()
    dry = move_completed(str(db), client, dry_run=True, now=NOW)
    assert dry["planned"][0]["media_id"] == "movie:tmdb:1"
    assert client.calls == []

    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert report["moved"][0]["info_hash"] == hash_
    assert client.calls == [(hash_, "/mnt/library/archive")]
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT status FROM media_items WHERE id=?", (media_id,)
    ).fetchone()[0] == "MOVING_TO_1FICHIER"
    conn.close()


def test_move_skips_active_stream(tmp_path):
    db, media_id, _ = seed(tmp_path)
    touch(str(db), "movie:tmdb:1", "sess", now=NOW)
    assert active(str(db), "movie:tmdb:1", grace_seconds=120, now=NOW)
    client = FakeMoveClient()
    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_streaming"] == ["movie:tmdb:1"]
    assert client.calls == []


def test_move_skips_stream_when_media_id_empty(tmp_path):
    db, media_id, _ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE media_items SET media_id='' WHERE id=?", (media_id,))
    conn.commit()
    conn.close()
    touch(str(db), "movie:tmdb:1", "sess", now=NOW)
    client = FakeMoveClient()
    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_streaming"] == ["movie:tmdb:1"]
    assert client.calls == []


def test_move_waits_after_completion(tmp_path):
    db, _media_id, _ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE torrents SET completed_at=?", (NOW.isoformat(),)
    )
    conn.commit()
    conn.close()
    client = FakeMoveClient()
    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert report["skipped_recent"] == ["movie:tmdb:1"]
    assert client.calls == []


def test_move_skips_public_and_missing_media(tmp_path):
    db, _media_id, _ = seed(tmp_path, privacy="public")
    client = FakeMoveClient()
    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert report["checked"] == 0
    assert client.calls == []


def test_lease_expiry_cleanup(tmp_path):
    db, media_id, _ = seed(tmp_path)
    touch(str(db), "movie:tmdb:1", "sess", now=NOW)
    from datetime import timedelta
    later = NOW + timedelta(seconds=1)
    assert cleanup(str(db), before_seconds=0, now=later) == 1
    assert not active(str(db), "movie:tmdb:1", now=later)


def test_move_skips_pack_when_any_episode_is_playing(tmp_path):
    db, _media_id, _ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,season,episode,title,status) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("series:tmdb:9:1:2", "series:tmdb:9:1:2", "series", 9, 1, 2,
         "Ep2", "SEEDING_LOCAL"),
    )
    episode_id = conn.execute(
        "SELECT id FROM media_items WHERE key='series:tmdb:9:1:2'"
    ).fetchone()[0]
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,torrent_id,file_index,rel_path,"
        "size_bytes,state,storage_location) VALUES(?,?,?,?,?,?,?)",
        (episode_id, torrent_id, 1, "Ep2.mkv", 1000, "ready", "LOCAL_DISK"),
    )
    conn.commit()
    conn.close()
    touch(str(db), "series:tmdb:9:1:2", "sess", now=NOW)
    client = FakeMoveClient()
    report = move_completed(str(db), client, dry_run=False, now=NOW)
    assert "series:tmdb:9:1:2" in report["skipped_streaming"]
    assert client.calls == []
