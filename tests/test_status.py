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

    MB = 1024**2
    # Sin duración: ~12 s de vídeo (size/240), con suelo de 8 MB.
    assert _buffer_required(5_861_122_731) == int(5_861_122_731 / 240)
    assert 8 * MB <= _buffer_required(5_861_122_731) <= 64 * MB
    assert _buffer_required(63_257_423_054) == 64 * MB
    assert _buffer_required(None) == 8 * MB


def test_buffer_required_uses_video_seconds_with_runtime():
    from catalogo.status import _buffer_required

    # 4,088 GB en 48 min: 10 s de vídeo son ~14 MB, no 32 MB.
    required = _buffer_required(4_088_319_489, 2880)
    assert required == int(4_088_319_489 / 2880 * 10)
    assert required < 32 * 1024**2


def test_buffer_required_rounds_up_to_one_piece():
    from catalogo.status import _buffer_required

    MB = 1024**2
    size = 1_753_457_301
    # 10 s de vídeo son ~6,8 MB, pero el contador avanza en piezas de 16 MB.
    assert _buffer_required(size, 2580, 16 * MB) == 16 * MB
    # Con piezas pequeñas (o desconocidas) se mantiene el suelo de 8 MB.
    assert _buffer_required(size, 2580, MB) == 8 * MB
    assert _buffer_required(size, 2580) == 8 * MB
    # Un fichero más pequeño que una pieza no puede pedir más de su tamaño.
    assert _buffer_required(4 * MB, 100, 16 * MB) == 4 * MB
    # El techo de 64 MB sigue limitando los vídeos de mucho bitrate.
    assert _buffer_required(size, 100, 16 * MB) == 64 * MB


def test_buffer_dynamics_detects_stall_and_eta():
    from catalogo.status import _buffer_dynamics

    MB = 1024**2
    _buffer_dynamics("stall-hash", 0, 8 * MB, 32 * MB, now=100.0)
    stalled, eta = _buffer_dynamics("stall-hash", 0, 8 * MB, 32 * MB, now=110.0)
    assert stalled is True
    assert eta is None

    _buffer_dynamics("grow-hash", 0, 4 * MB, 32 * MB, now=200.0)
    stalled, eta = _buffer_dynamics("grow-hash", 0, 12 * MB, 32 * MB, now=210.0)
    assert stalled is False
    assert eta == int((32 - 12) * MB / ((8 * MB) / 10))


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
    # El suelo sigue siendo 8 MB cuando la pieza es más pequeña.
    assert required == 8 * 1024**2
    assert tail_ready is True

    # Si la cola del torrent no está lista para un fichero que coincide con el final:
    states[-1] = 0
    buffer_bytes, required, tail_ready = _buffer_state(
        BufferQbit(states), "hash", files, 0, size
    )
    assert tail_ready is False

    # Para un fichero en medio de un pack (no es cola del torrent), no se exige:
    pack_files = [
        {"index": 0, "size": size},
        {"index": 1, "size": size * 3},
    ]
    pack_states = [2] * 50 + [0] * 350
    buffer_bytes, required, tail_ready = _buffer_state(
        BufferQbit(pack_states), "hash", pack_files, 0, size
    )
    assert tail_ready is True


def test_waiting_reason_first_piece_while_downloading(tmp_path):
    from catalogo.status import asset_status

    db, _asset_id, _release_id = seed(tmp_path)
    size = 16 * 1024**2
    conn = sqlite3.connect(db)
    conn.execute("UPDATE releases SET size_bytes=?", (size,))
    conn.execute("UPDATE torrent_files SET size_bytes=?", (size,))
    conn.commit()
    conn.close()

    class DownloadingQbit(FakeStatusQbit):
        def files(self, info_hash):
            return [{"index": 0, "size": size, "progress": 0.1}]

        def properties(self, info_hash):
            return {"piece_size": size}

        def piece_states(self, info_hash):
            return [0, 0]

    qbit = DownloadingQbit([{
        "hash": "hash1", "state": "downloading", "progress": 0.1,
        "size": size, "downloaded": size // 10, "dlspeed": 5000,
        "num_seeds": 2, "num_leechs": 0, "num_complete": 3,
        "num_incomplete": 1,
    }])
    status = asset_status(db, "movie:tmdb:1", qbit=qbit)
    assert status["buffer_bytes"] == 0
    assert status["buffer_required"] == size
    assert status["waiting_reason"] == "esperando primera pieza"


def test_local_ready_status_with_frozen_release():
    from catalogo.status import local_ready_status
    from catalogo.resolver import Resolution

    class FrozenResolver:
        def resolve(self, key):
            return Resolution(
                key=key, asset_id=1, source="local_seed", reason="active_file",
                path="/tmp/Test.mkv", size=1000, release_id=99,
                file_id=2, file_index=0,
            )

    status = {
        "stream_ready": False,
        "source": "torrent",
        "buffer_bytes": 0,
        "waiting_reason": "buffer",
    }
    ready = local_ready_status(FrozenResolver(), "movie:tmdb:1", status)
    assert ready["stream_ready"] is True
    assert ready["state"] == "AVAILABLE"
    assert ready["buffer_bytes"] == 1000
    assert ready["waiting_reason"] is None
    # El estado original no se muta.
    assert status["stream_ready"] is False


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


def test_closed_seed_commitment_has_no_pending_deadline(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE seed_commitments SET state='done', seed_started_at=?, "
        "seed_required_until=NULL, target_ratio=NULL, info_hash='hash1'",
        ((now - timedelta(hours=2)).isoformat(),),
    )
    conn.commit()
    conn.close()
    upsert_policy(
        str(db),
        TrackerPolicy(
            "TrackerEspanol2 (API)", min_seed_hours=0, target_ratio=0,
            max_active=2, allow_partial=False, privacy="public",
        ),
    )
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["seed"]["state"] == "done"
    assert status["seed"]["required_until"] is None
    assert status["seed"]["remaining_seconds"] is None
    assert status["seed_required_until"] is None


def test_seed_status_reports_library_ready(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE seed_commitments SET seed_started_at=?, "
        "seed_required_until=NULL, info_hash='hash1'",
        ((now - timedelta(hours=2)).isoformat(),),
    )
    conn.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,source) "
        "VALUES('movie',1,0,0,'/mnt/fichier/Test.mkv','radarr')"
    )
    conn.commit()
    conn.close()
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["seed"]["library_ready"] is True


def _add_intent(conn, key, created, ready_ms=1000, first_ms=2000):
    from datetime import timedelta

    created_at = created
    ready_at = created + timedelta(milliseconds=ready_ms)
    first_at = created + timedelta(milliseconds=first_ms)
    expires = created + timedelta(minutes=5)
    conn.execute(
        "INSERT INTO play_intents(media_id,source,created_at,expires_at,"
        "consumed_at,ready_at,first_byte_at) VALUES(?,?,?,?,?,?,?)",
        (key, "range", created_at.isoformat(), expires.isoformat(),
         created_at.isoformat(), ready_at.isoformat(), first_at.isoformat()),
    )


def test_startup_metrics_use_the_first_intent_of_the_session(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    _add_intent(
        conn, "movie:tmdb:1", now - timedelta(seconds=120),
        ready_ms=500, first_ms=700,
    )
    _add_intent(conn, "movie:tmdb:1", now - timedelta(seconds=60))
    _add_intent(conn, "movie:tmdb:1", now - timedelta(seconds=5))
    conn.commit()
    conn.close()
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["prepare_ms"] == 500
    assert status["first_byte_ms"] == 700


def test_startup_metrics_reset_after_a_long_gap(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    db, _asset_id, _release_id = seed(tmp_path, complete=True, local=True)
    conn = sqlite3.connect(db)
    _add_intent(
        conn, "movie:tmdb:1", now - timedelta(hours=3),
        ready_ms=500, first_ms=700,
    )
    _add_intent(
        conn, "movie:tmdb:1", now - timedelta(seconds=5),
        ready_ms=1500, first_ms=1800,
    )
    conn.commit()
    conn.close()
    status = asset_status(db, "movie:tmdb:1", qbit=None, now=now)
    assert status["prepare_ms"] == 1500
    assert status["first_byte_ms"] == 1800
