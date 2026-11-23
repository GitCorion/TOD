import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.status import asset_status

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeStatusQbit:
    def __init__(self, torrents):
        self._torrents = torrents

    def torrents(self, category=None):
        return list(self._torrents)


def seed(tmp_path, *, torrent=True, complete=False, local=False):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"x" * 1000)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2024, "COLD"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2 (API)", "Test.mkv", 1000, "alive",
         "prowlarr", "private"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    if torrent:
        conn.execute(
            "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
            "progress,qbit_state,save_path) VALUES(?,?,?,?,?,?,?,?)",
            ("hash1", release_id, "TrackerEspanol2 (API)", "seeding" if complete
             else "downloading", "ondemand", 1 if complete else 0.5,
             "stalledUP" if complete else "downloading", "/mnt/descargas/ondemand"),
        )
        torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
        conn.execute(
            "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
            "rel_path,size_bytes,state,storage_location,local_path) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (asset_id, release_id, torrent_id, 0, "Test.mkv", 1000,
             "ready" if local else "downloading", "LOCAL_DISK",
             str(media) if local else None),
        )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,state) "
        "VALUES(?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2 (API)", 1000, "active"),
    )
    conn.commit()
    conn.close()
    return db, asset_id, release_id


def test_downloading_status(tmp_path):
    db, _asset_id, release_id = seed(tmp_path)
    qbit = FakeStatusQbit([{
        "hash": "hash1", "state": "downloading", "progress": 0.5,
        "size": 1000, "downloaded": 500, "dlspeed": 1234, "eta": 60,
        "num_seeds": 4, "num_leechs": 2, "num_complete": 20,
        "num_incomplete": 3,
    }])
    status = asset_status(db, "movie:tmdb:1", qbit=qbit)
    assert status["state"] == "DOWNLOADING"
    assert status["release_id"] == release_id
    assert status["progress"] == 0.5
    assert status["speed_bps"] == 1234
    assert status["eta_seconds"] == 60
    assert status["seeds"] == 4
    assert status["stream_ready"] is False
    assert status["seed_state"] == "active"


def test_available_status(tmp_path):
    db, _asset_id, _release_id = seed(
        tmp_path, complete=True, local=True
    )
    qbit = FakeStatusQbit([{
        "hash": "hash1", "state": "stalledUP", "progress": 1,
        "size": 1000, "downloaded": 1000, "dlspeed": 0, "eta": 0,
        "num_seeds": 0, "num_peers": 0,
    }])
    status = asset_status(db, "movie:tmdb:1", qbit=qbit)
    assert status["state"] == "SEEDING"
    assert status["stream_ready"] is True
    assert status["source"] in ("library", "local_seed")
    assert status["progress"] == 1


def test_cold_status_without_torrent(tmp_path):
    db, _asset_id, release_id = seed(tmp_path, torrent=False)
    status = asset_status(db, "movie:tmdb:1", qbit=None)
    assert status["state"] == "COLD"
    assert status["release_id"] == release_id
    assert status["progress"] == 0
    assert status["stream_ready"] is False


def test_release_must_belong_to_asset(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    assert asset_status(db, "movie:tmdb:1", release_id=999) is None


def test_unknown_key(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    assert asset_status(db, "movie:tmdb:999") is None


def test_live_progress_100_marks_complete_without_worker(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE torrents SET save_path=? WHERE info_hash='hash1'",
        (str(tmp_path),),
    )
    conn.execute(
        "UPDATE torrent_files SET rel_path='Movie.mkv' "
        "WHERE torrent_id IS NOT NULL"
    )
    conn.commit()
    conn.close()
    qbit = FakeStatusQbit([{
        "hash": "hash1", "state": "stalledUP", "progress": 1,
        "size": 1000, "downloaded": 1000, "dlspeed": 0, "eta": 0,
        "num_seeds": 0, "num_leechs": 0, "num_complete": 1,
        "num_incomplete": 0,
    }])
    status = asset_status(db, "movie:tmdb:1", qbit=qbit)
    assert status["source"] == "local_seed"
    assert status["stream_ready"] is True
    assert status["progress"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM torrent_files WHERE torrent_id IS NOT NULL"
    ).fetchone()[0] == "ready"
    conn.close()


def test_buffer_required_scales_with_size():
    from catalogo.status import _buffer_required

    assert _buffer_required(5_861_122_731) >= 32 * 1024**2
    assert _buffer_required(5_861_122_731) <= 128 * 1024**2
    assert _buffer_required(63_257_423_054) == 128 * 1024**2
    assert _buffer_required(None) == 32 * 1024**2


class BufferQbit:
    def __init__(self, states):
        self.states = states

    def properties(self, info_hash):
        return {"piece_size": 1024 * 1024}

    def piece_states(self, info_hash):
        return self.states


def test_buffer_state_counts_contiguous_from_start():
    from catalogo.status import _buffer_state

    size = 100 * 1024 * 1024
    states = [2] * 50 + [0] * 50
    # cola lista para que el probe de Jellyfin no falle
    states[-1] = 2
    files = [{"index": 0, "size": size}]
    buffer_bytes, required, tail_ready = _buffer_state(
        BufferQbit(states), "hash", files, 0, size
    )
    assert buffer_bytes == 50 * 1024 * 1024
    assert required == 32 * 1024**2
    assert tail_ready is True


def test_playback_state_preparing_and_startup_metrics(tmp_path):
    from datetime import datetime, timedelta, timezone

    from catalogo import play_intents

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path)
    intent = play_intents.create(
        db, "movie:tmdb:1", ttl_minutes=5, now=now
    )
    play_intents.ready(db, intent["id"], now=now + timedelta(seconds=2))
    play_intents.first_byte(
        db, intent["id"], now=now + timedelta(seconds=3)
    )

    status = asset_status(
        db, "movie:tmdb:1", qbit=None, now=now + timedelta(seconds=10)
    )
    assert status["playback_state"] == "preparing"
    assert status["intent_id"] == intent["id"]
    assert status["prepare_ms"] == 2000
    assert status["first_byte_ms"] == 3000
    assert status["switch_count"] == 0


def test_playback_state_switching_wins_over_preparing(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, torrent=False)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE media_items SET stream_generation=2, stream_switched_at=?, "
        "stream_switch_reason='slow_swarm'",
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()

    status = asset_status(
        db, "movie:tmdb:1", qbit=None, now=now + timedelta(seconds=5)
    )
    assert status["playback_state"] == "switching"
    assert status["switch_count"] == 2
    assert status["stream_switch_reason"] == "slow_swarm"


def test_playback_state_ready_when_local_file_exists(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    status = asset_status(db, "movie:tmdb:1", qbit=None)
    assert status["stream_ready"] is True
    assert status["playback_state"] == "ready"


def test_playback_state_is_idle_without_progress_or_intent(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path, torrent=False)
    status = asset_status(db, "movie:tmdb:1", qbit=None)
    assert status["playback_state"] == "idle"
    assert status["prepare_ms"] is None
    assert status["first_byte_ms"] is None


def test_seed_status_counts_from_completion(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE seed_commitments SET seed_started_at=?, "
        "seed_required_until=NULL, target_ratio=1.0, info_hash='hash1'",
        ((now - timedelta(hours=2)).isoformat(),),
    )
    conn.execute("UPDATE torrents SET ratio=0.5 WHERE info_hash='hash1'")
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol2 (API)", min_seed_hours=24, target_ratio=1.0,
            max_active=2, allow_partial=False, privacy="private",
        ),
    )
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["seed"]["remaining_seconds"] == 22 * 3600
    assert status["seed_required_until"].startswith(
        (now + timedelta(hours=22)).isoformat()[:19]
    )
    assert status["seed"]["target_ratio"] == 1.0
    assert status["seed"]["ratio"] == 0.5
    assert status["seed"]["tracker_verdict"] is None


def test_seed_status_ignores_closed_commitments(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, asset_id, release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,"
        "state,seed_required_until) VALUES(?,?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2 (API)", 1000, "cancelled",
         (now + timedelta(days=60)).isoformat()),
    )
    conn.execute(
        "UPDATE seed_commitments SET seed_started_at=?, "
        "seed_required_until=NULL WHERE state='active'",
        ((now - timedelta(hours=2)).isoformat(),),
    )
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol2 (API)", min_seed_hours=24, target_ratio=1.0,
            max_active=2, allow_partial=False, privacy="private",
        ),
    )
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["seed"]["state"] == "active"
    assert status["seed"]["remaining_seconds"] == 22 * 3600
