import sqlite3
from pathlib import Path

from catalogo.db import init_db
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
