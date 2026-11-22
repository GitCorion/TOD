import io
import sqlite3
from pathlib import Path

import pytest

from catalogo import play_intents, stream as stream_module
from catalogo.backend import info_hash
from catalogo.db import init_db
from catalogo.play_service import NoPeers, PlayService, Warming
from catalogo.policy import TrackerPolicy, upsert_policy

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
TORRENT = b"d4:info4:teste"
MB = 1024 * 1024


class FakeQbit:
    def __init__(self, files):
        self.files_list = files
        self.torrents_list = []
        self.added = []
        self.priorities = []
        self.forced = []
        self.resumed = []

    def torrents(self, category=None):
        return list(self.torrents_list)

    def files(self, info_hash):
        return list(self.files_list)

    def properties(self, info_hash):
        return {"piece_size": MB}

    def piece_states(self, info_hash):
        return [2]

    def add_torrent(self, torrent_bytes, **kwargs):
        self.added.append(kwargs)
        self.torrents_list.append(
            {"hash": info_hash(torrent_bytes), "state": "pausedDL"}
        )
        return "Ok."

    def set_file_priorities(self, info_hash, priorities):
        self.priorities.append((info_hash, priorities))

    def set_force_start(self, info_hash, value=True):
        self.forced.append((info_hash, value))

    def toggle_first_last(self, info_hash):
        pass

    def pause(self, info_hash):
        self.paused = getattr(self, "paused", [])
        self.paused.append(info_hash)

    def resume(self, info_hash):
        self.resumed.append(info_hash)
        for torrent in self.torrents_list:
            if (torrent.get("hash") or "").lower() == info_hash.lower():
                torrent["state"] = "downloading"


class FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


def seed(tmp_path, privacy="public", policy=True):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:300", "movie:tmdb:300", "movie", 300, "Test", 2025, "COLD"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy,seeders) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "Publico1" if privacy == "public" else "TrackerEspanol (API)",
         "Test.mkv", 1000, "alive", "jackett" if privacy == "public" else "prowlarr",
         privacy, 5),
    )
    conn.commit()
    conn.close()
    if policy:
        upsert_policy(
            str(db),
            TrackerPolicy(
                "Publico1" if privacy == "public" else "TrackerEspanol (API)",
                min_seed_hours=0 if privacy == "public" else 120,
                target_ratio=0, max_active=2,
                allow_partial=privacy == "public",
                privacy=privacy,
            ),
        )
    play_intents.create(str(db), "movie:tmdb:300")
    return db


def test_preflight_fails_without_peers_and_passes_with_progress(tmp_path):
    db = seed(tmp_path)
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    handle = {
        "info_hash": "hash",
        "release_id": 1,
        "file_index": 0,
        "private": True,
    }
    qbit.torrents_list = [{
        "hash": "hash", "state": "forcedDL", "progress": 0,
        "num_complete": 0, "num_seeds": 0, "dlspeed": 0,
    }]
    with pytest.raises(NoPeers):
        service.preflight(handle, timeout=1.0)

    qbit.torrents_list[0].update({"state": "downloading", "progress": 0.1})
    service.preflight(handle, timeout=1.0)


def test_preflight_warms_when_swarm_has_peers(tmp_path):
    db = seed(tmp_path)
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    qbit.torrents_list = [{
        "hash": "hash", "state": "downloading", "progress": 0,
        "num_complete": 34, "num_seeds": 0, "dlspeed": 0,
    }]
    handle = {"info_hash": "hash", "release_id": 2, "file_index": 0,
              "private": True}
    with pytest.raises(Warming):
        service.preflight(handle, timeout=1.0)


def test_play_service_admits_prepares_and_streams(tmp_path, monkeypatch):
    db = seed(tmp_path)
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"Z" * 1000)
    monkeypatch.setattr(stream_module, "STREAM_ROOTS", [str(tmp_path)])
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    handle = service.ensure("movie:tmdb:300", torrent_bytes=TORRENT)
    assert handle["info_hash"] == info_hash(TORRENT)
    assert qbit.added[0]["paused"] is True
    assert qbit.priorities[0][1] == {0: 1}
    assert qbit.forced == [(info_hash(TORRENT), True)]
    assert qbit.resumed == [info_hash(TORRENT)]

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT state FROM seed_commitments").fetchone()[0] == "active"
    conn.close()
    assert play_intents.active(str(db), "movie:tmdb:300") is None

    # Segundo play: reutiliza torrent y commitment.
    play_intents.create(str(db), "movie:tmdb:300")
    handler = FakeHandler()
    service.stream(handler, "movie:tmdb:300", 0, 99, torrent_bytes=TORRENT)
    assert handler.status == 206
    assert handler.wfile.getvalue() == b"Z" * 100
    assert qbit.resumed == [info_hash(TORRENT)]
    assert qbit.forced == [(info_hash(TORRENT), True)]
    assert len(qbit.added) == 1


def test_ensure_falls_back_to_next_release(tmp_path, monkeypatch):
    from catalogo import backend

    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    second = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "source,privacy,seeders) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "Publico1", "Test.720p.mkv", 500, "alive",
         "jackett", "public", 9),
    ).lastrowid
    conn.commit()
    conn.close()

    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 500}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    calls = {"n": 0}

    def fetch(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise backend.TorrentFetchError("HTTP 404", status=404, permanent=True)
        return TORRENT

    monkeypatch.setattr(backend, "fetch_torrent", fetch)
    handle = service.ensure("movie:tmdb:300", release_id=1)
    assert handle["release_id"] == second
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT health FROM releases WHERE id=1").fetchone()[0] == "dead"
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE release_id=?", (second,)
    ).fetchone()[0] == "active"
    conn.close()


def test_ensure_raises_no_source_when_all_fail(tmp_path, monkeypatch):
    from catalogo import backend
    from catalogo.backend import NoSource

    db = seed(tmp_path)
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )

    def fetch(url, **kwargs):
        raise backend.TorrentFetchError("HTTP 404", status=404, permanent=True)

    monkeypatch.setattr(backend, "fetch_torrent", fetch)
    with pytest.raises(NoSource) as info:
        service.ensure("movie:tmdb:300")
    assert info.value.attempts
    assert info.value.attempts[0]["error"] == "release_unavailable"


def test_ensure_accepts_unknown_health_with_seeds(tmp_path):
    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE releases SET health='unknown'")
    conn.commit()
    conn.close()
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    handle = service.ensure("movie:tmdb:300", torrent_bytes=TORRENT)
    assert handle["release_id"] == 1


def test_ensure_runtime_fills_from_tmdb(tmp_path, monkeypatch):
    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE media_items SET tmdb=123, runtime_seconds=NULL")
    conn.commit()
    conn.close()
    from catalogo import tmdb

    monkeypatch.setattr(
        tmdb.TmdbClient,
        "movie_details",
        lambda self, tmdb_id: {"runtime": 100},
    )
    qbit = FakeQbit([{"index": 0, "name": "Movie.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
        tmdb_api_key="x",
    )
    assert service.ensure_runtime("movie:tmdb:300") == 6000
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT runtime_seconds FROM media_items"
    ).fetchone()[0] == 6000
    conn.close()


def test_ensure_runtime_for_series_episode(tmp_path, monkeypatch):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    key = "series:tmdb:200:1:2"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,season,episode,title) "
        "VALUES(?,?,?,?,?,?,?)",
        (key, key, "series", 200, 1, 2, "Test"),
    )
    conn.commit()
    conn.close()
    from catalogo import tmdb

    monkeypatch.setattr(
        tmdb.TmdbClient,
        "season_details",
        lambda self, tmdb_id, season: {
            "episodes": [
                {"episode_number": 1, "runtime": 40},
                {"episode_number": 2, "runtime": 50},
            ]
        },
    )
    qbit = FakeQbit([{"index": 0, "name": "Show.S01E02.mkv", "size": 1000}])
    service = PlayService(
        str(db), qbit, qbit, data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
        tmdb_api_key="x",
    )
    assert service.ensure_runtime(key) == 3000
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT runtime_seconds FROM media_items"
    ).fetchone()[0] == 3000
    conn.close()
