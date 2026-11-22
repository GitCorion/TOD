import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.resolver import Resolver
from catalogo.stream_guard import StreamGuard, failed_release_ids
from catalogo.stream_leases import touch

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
MB = 1024 * 1024


class FakeQbit:
    def __init__(self, *, progress=0.02, speed=0, buffer_pieces=0):
        self.deleted = []
        self.paused = []
        self.progress = progress
        self.speed = speed
        self.buffer_pieces = buffer_pieces

    def torrent(self, info_hash):
        return {
            "hash": info_hash, "state": "downloading",
            "progress": self.progress, "dlspeed": self.speed,
            "size": 200 * MB,
        }

    def files(self, info_hash):
        return [{"index": 0, "name": "Movie.mkv", "size": 200 * MB,
                 "progress": self.progress}]

    def properties(self, info_hash):
        return {"piece_size": MB}

    def piece_states(self, info_hash):
        total = 200
        states = [0] * total
        for index in range(self.buffer_pieces):
            states[index] = 2
        # cola lista
        states[-1] = 2
        return states

    def delete(self, info_hash, *, delete_files=False):
        self.deleted.append((info_hash, delete_files))

    def pause(self, info_hash):
        self.paused.append(info_hash)


class FakePlayService:
    def __init__(self, qbit):
        self.qbit_write = qbit
        self.calls = []

    def ensure(self, key):
        self.calls.append(key)
        return {"info_hash": "next"}


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status,"
        "runtime_seconds) VALUES(?,?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2024,
         "DOWNLOADING_LOCAL", 7200),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "source,privacy,seeders) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol3 (API)", "Movie.mkv", 200 * MB, "alive",
         "prowlarr", "private", 15),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress,qbit_state,save_path) VALUES(?,?,?,?,?,?,?,?)",
        ("hash1", release_id, "TrackerEspanol3 (API)", "downloading", "ondemand",
         0.02, "downloading", "/mnt/descargas/ondemand"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,size_bytes,state,storage_location) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "Movie.mkv", 200 * MB,
         "downloading", "LOCAL_DISK"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,"
        "state) VALUES(?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol3 (API)", 200 * MB, "active"),
    )
    conn.commit()
    conn.close()
    touch(str(db), "movie:tmdb:1", "sess", now=NOW)
    return db, asset_id, release_id


def test_guard_deletes_early_bad_swarm_and_tries_next(tmp_path):
    db, asset_id, release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.02, speed=0, buffer_pieces=0)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    first = guard.run_once(now=NOW)
    assert first["bad"] == []
    second = guard.run_once(now=NOW + timedelta(seconds=40))
    assert second["bad"][0]["reason"] == "no_contiguous_buffer"
    assert qbit.deleted == [("hash1", True)]
    assert service.calls == ["movie:tmdb:1"]
    assert release_id in failed_release_ids(str(db), asset_id=asset_id)
    assert Resolver(db).sources("movie:tmdb:1") == []


def test_guard_keeps_torrent_over_five_percent(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.06, speed=0, buffer_pieces=0)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    guard.run_once(now=NOW + timedelta(seconds=40))
    assert qbit.deleted == []
    assert qbit.paused == ["hash1"]


def test_guard_starts_when_buffer_is_enough(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.05, speed=10 * MB, buffer_pieces=40)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    report = guard.run_once(now=NOW + timedelta(seconds=40))
    assert report["bad"] == []
    assert service.calls == []


def test_switch_increments_stream_generation(tmp_path):
    db, asset_id, _release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.02, speed=0, buffer_pieces=0)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    guard.run_once(now=NOW + timedelta(seconds=40))
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT stream_generation, stream_switch_reason FROM media_items "
        "WHERE id=?", (asset_id,)
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "no_contiguous_buffer"


def test_guard_switches_when_audio_is_not_spanish(tmp_path, monkeypatch):
    from catalogo import stream_guard as guard_module

    db, _asset_id, _release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.05, speed=10 * MB, buffer_pieces=40)
    service = FakePlayService(qbit)
    from catalogo.media_probe import AudioProbe

    monkeypatch.setattr(
        guard_module,
        "probe_audio",
        lambda path: AudioProbe(("eng", "fre"), False),
    )
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    report = guard.run_once(now=NOW + timedelta(seconds=40))
    assert report["bad"][0]["reason"] == "audio_not_spanish"
    assert qbit.deleted == [("hash1", True)]


class BusyQbit(FakeQbit):
    def torrents(self):
        return [
            {"hash": f"check{i}", "state": "checkingUP"}
            for i in range(60)
        ]


def test_guard_does_not_blame_source_while_qbit_is_checking(tmp_path):
    db, _asset_id, _release_id = seed(tmp_path)
    qbit = BusyQbit(progress=0.02, speed=0, buffer_pieces=0)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    report = guard.run_once(now=NOW + timedelta(seconds=40))
    assert report["qbit_busy"] is True
    assert report["bad"] == []
    assert report["deferred"][0]["reason"] == "qbit_busy"
    assert qbit.deleted == []
    assert qbit.paused == []


def test_guard_records_swarm_stats(tmp_path):
    db, _asset_id, release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.02, speed=0, buffer_pieces=0)
    service = FakePlayService(qbit)
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    guard.run_once(now=NOW + timedelta(seconds=40))
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT started_ok, failures FROM swarm_stats WHERE release_id=?",
        (release_id,),
    ).fetchone()
    conn.close()
    assert row == (0, 1)


def test_guard_does_not_switch_on_unknown_audio(tmp_path, monkeypatch):
    from catalogo import stream_guard as guard_module
    from catalogo.media_probe import AudioProbe

    db, _asset_id, _release_id = seed(tmp_path)
    qbit = FakeQbit(progress=0.05, speed=10 * MB, buffer_pieces=40)
    service = FakePlayService(qbit)
    monkeypatch.setattr(
        guard_module,
        "probe_audio",
        lambda path: AudioProbe(("eng",), True),
    )
    guard = StreamGuard(str(db), service, grace_seconds=30)
    guard.run_once(now=NOW)
    report = guard.run_once(now=NOW + timedelta(seconds=40))
    assert report["bad"] == []
    assert qbit.deleted == []
    assert service.calls == []
