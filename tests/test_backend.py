import sqlite3
from pathlib import Path

import pytest

from catalogo import play_intents
from catalogo.backend import (
    CapacityError,
    NoPlayIntent,
    NoPolicy,
    NoSeedCommitment,
    info_hash,
    prepare_download,
    priorities_for,
)
from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
TORRENT = b"d4:info4:teste"


class FakeQbit:
    def __init__(self, files=None, existing=None):
        self.files_list = files or []
        self.torrents_list = list(existing or [])
        self.added = []
        self.priorities = []
        self.forced = []
        self.resumed = []
        self.toggles = []

    def torrents(self, category=None):
        return list(self.torrents_list)

    def files(self, info_hash):
        return list(self.files_list)

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
        self.toggles.append(info_hash)

    def pause(self, info_hash):
        self.paused = getattr(self, "paused", [])
        self.paused.append(info_hash)

    def resume(self, info_hash):
        self.resumed.append(info_hash)
        for torrent in self.torrents_list:
            if (torrent.get("hash") or "").lower() == info_hash.lower():
                torrent["state"] = "downloading"

    def delete(self, info_hash, delete_files=False):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append((info_hash, delete_files))
        self.torrents_list = [
            torrent for torrent in self.torrents_list
            if (torrent.get("hash") or "").lower() != info_hash.lower()
        ]


def seed(tmp_path, *, kind="series", tracker="TrackerEspanol2 (API)",
         privacy="private", allow_partial=False, with_intent=True,
         with_commit=True, with_policy=True):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    key = "series:tmdb:200:1:2" if kind == "series" else "movie:tmdb:300"
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, season, episode, "
        "title, year) VALUES(?,?,?,?,?,?,?,?)",
        (key, key, kind, 200 if kind == "series" else 300,
         1 if kind == "series" else None,
         2 if kind == "series" else None,
         "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, tracker, "Test.pack", 1000, "alive", "prowlarr", privacy),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    if with_commit:
        conn.execute(
            "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,state) "
            "VALUES(?,?,?,?,?)",
            (asset_id, release_id, tracker, 1000, "approved"),
        )
    conn.commit()
    conn.close()
    if with_policy:
        upsert_policy(
            str(db),
            TrackerPolicy(
                tracker, min_seed_hours=120, target_ratio=0, max_active=2,
                allow_partial=allow_partial, keep_for_bonus=True,
                privacy=privacy,
            ),
        )
    if with_intent:
        play_intents.create(str(db), key)
    return db, key, asset_id


def test_existing_running_torrent_is_not_paused_or_resumed(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[
            {
                "hash": info_hash(TORRENT),
                "state": "forcedDL",
                "progress": 0.1,
            }
        ],
    )
    result = prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert result["already_running"] is True
    assert result["resumed"] is False
    assert client.added == []
    assert getattr(client, "paused", []) == []
    assert client.toggles == []
    assert client.resumed == []
    assert client.forced == []
    assert client.priorities


def test_existing_paused_torrent_is_started_once(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[
            {"hash": info_hash(TORRENT), "state": "pausedDL", "progress": 0}
        ],
    )
    result = prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert result["resumed"] is True
    assert result["already_running"] is False
    assert client.added == []
    assert getattr(client, "paused", []) == []
    assert client.resumed == [info_hash(TORRENT)]
    assert client.forced == [(info_hash(TORRENT), True)]


def test_private_pack_prioritizes_episode_but_downloads_all(tmp_path):
    db, key, _ = seed(tmp_path)
    client = FakeQbit(
        files=[
            {"index": 0, "name": "Show.S01E01.mkv", "size": 100},
            {"index": 1, "name": "Show.S01E02.mkv", "size": 200},
            {"index": 2, "name": "Show.S01E03.mkv", "size": 300},
            {"index": 3, "name": "readme.txt", "size": 1},
        ]
    )
    result = prepare_download(
        str(db), key, client, torrent_bytes=TORRENT, save_path="/mnt/multimedia/ondemand"
    )
    assert result["private"] is True
    assert result["file_index"] == 1
    assert client.added[0]["paused"] is True
    assert client.added[0]["category"] == "ondemand"
    assert client.added[0]["use_download_path"] is False
    assert client.priorities[0][1] == {0: 1, 1: 6, 2: 1, 3: 1}
    assert client.forced == [(info_hash(TORRENT), True)]
    assert client.resumed == [info_hash(TORRENT)]
    assert getattr(client, "paused", []) == []
    assert client.toggles == [info_hash(TORRENT)] * 2

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT state FROM seed_commitments"
    ).fetchone()[0] == "active"
    mapped = conn.execute(
        "SELECT asset_id, file_index FROM torrent_files WHERE asset_id IS NOT NULL"
    ).fetchall()
    assert mapped == [(1, 1)]
    assert conn.execute(
        "SELECT status, active_file_id FROM media_items"
    ).fetchone()[0] == "DOWNLOADING_LOCAL"
    assert conn.execute(
        "SELECT category, state FROM torrents WHERE info_hash=?",
        (info_hash(TORRENT),),
    ).fetchone() == ("ondemand", "waiting")
    conn.close()
    assert play_intents.active(str(db), key) is None


def test_public_pack_only_downloads_target(tmp_path):
    db, key, _ = seed(
        tmp_path, kind="movie", tracker="Publico1", privacy="public",
        allow_partial=True,
    )
    client = FakeQbit(
        files=[
            {"index": 0, "name": "Movie.mkv", "size": 1000},
            {"index": 1, "name": "sample.mkv", "size": 10},
        ]
    )
    result = prepare_download(
        str(db), key, client, torrent_bytes=TORRENT
    )
    assert result["private"] is False
    assert result["file_index"] == 0
    assert client.priorities[0][1] == {0: 1, 1: 0}


def test_requires_play_intent(tmp_path):
    db, key, _ = seed(tmp_path, with_intent=False)
    client = FakeQbit(files=[{"index": 0, "name": "x.mkv", "size": 1}])
    with pytest.raises(NoPlayIntent):
        prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert client.added == []


def test_requires_seed_commitment(tmp_path):
    db, key, _ = seed(tmp_path, with_commit=False)
    client = FakeQbit(files=[{"index": 0, "name": "x.mkv", "size": 1}])
    with pytest.raises(NoSeedCommitment):
        prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert client.added == []


def test_requires_policy(tmp_path):
    db, key, _ = seed(tmp_path, with_policy=False)
    client = FakeQbit(files=[{"index": 0, "name": "x.mkv", "size": 1}])
    with pytest.raises(NoPolicy):
        prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert client.added == []


def test_capacity_blocks_third_active(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="Publico1",
                       privacy="public", allow_partial=True)
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[
            {"hash": "a", "state": "downloading"},
            {"hash": "b", "state": "downloading"},
        ],
    )
    with pytest.raises(CapacityError):
        prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert client.added == []


def test_existing_error_torrent_is_removed_and_readded(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[
            {"hash": info_hash(TORRENT), "state": "error", "progress": 0}
        ],
    )
    result = prepare_download(str(db), key, client, torrent_bytes=TORRENT)
    assert client.deleted
    assert client.deleted[0][0] == info_hash(TORRENT)
    assert client.added
    assert result["resumed"] is True


def test_fetch_failure_marks_release_dead(tmp_path, monkeypatch):
    from catalogo import backend

    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(files=[{"index": 0, "name": "Movie.mkv", "size": 1000}])

    def broken(*args, **kwargs):
        raise backend.TorrentFetchError(
            "el índice devolvió HTTP 404", status=404, permanent=True
        )

    monkeypatch.setattr(backend, "fetch_torrent", broken)
    with pytest.raises(backend.ReleaseUnavailable) as info:
        prepare_download(str(db), key, client)
    assert info.value.permanent is True
    assert info.value.release_id == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT health FROM releases WHERE id=1").fetchone()[0] == "dead"
    assert conn.execute("SELECT state FROM seed_commitments").fetchone()[0] == "cancelled"
    conn.close()


def test_transient_fetch_failure_keeps_release_alive(tmp_path, monkeypatch):
    from catalogo import backend

    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(files=[{"index": 0, "name": "Movie.mkv", "size": 1000}])

    def timeout(*args, **kwargs):
        raise backend.TorrentFetchError("timeout", permanent=False)

    monkeypatch.setattr(backend, "fetch_torrent", timeout)
    with pytest.raises(backend.ReleaseUnavailable) as info:
        prepare_download(str(db), key, client)
    assert info.value.permanent is False
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT health FROM releases WHERE id=1").fetchone()[0] == "alive"
    assert conn.execute("SELECT state FROM seed_commitments").fetchone()[0] == "approved"
    conn.close()


def test_force_bypasses_capacity_and_forces_start(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(files=[{"index": 0, "name": "Movie.mkv", "size": 1000}])
    result = prepare_download(
        str(db), key, client, torrent_bytes=TORRENT, max_active=0, force=True
    )
    assert result["forced"] is True
    assert client.forced == [(info_hash(TORRENT), True)]


def test_force_restarts_existing_queued_torrent(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    digest = info_hash(TORRENT)
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[{"hash": digest, "state": "queuedDL"}],
    )
    result = prepare_download(
        str(db), key, client, torrent_bytes=TORRENT, max_active=0, force=True
    )
    assert result["forced"] is True
    assert (digest, True) in client.forced
    assert digest in client.resumed


def test_capacity_ignores_queued_and_checking(tmp_path):
    db, key, _ = seed(tmp_path, kind="movie", tracker="TrackerEspanol2 (API)")
    client = FakeQbit(
        files=[{"index": 0, "name": "Movie.mkv", "size": 1000}],
        existing=[
            {"hash": "q1", "state": "queuedDL"},
            {"hash": "q2", "state": "queuedDL"},
            {"hash": "c1", "state": "checkingDL"},
            {"hash": "p1", "state": "pausedDL"},
        ],
    )
    result = prepare_download(
        str(db), key, client, torrent_bytes=TORRENT, max_active=4
    )
    assert result["info_hash"] == info_hash(TORRENT)
    assert len(client.added) == 1


def test_priorities_private_target_high_not_max():
    """Prioridad 6 mantiene el orden secuencial; la 7 lo rompe."""
    files = [{"index": 0}, {"index": 1}]
    assert priorities_for(files, 0, private=True) == {0: 6, 1: 1}


def test_priorities_private_single_file_normal():
    """Con un solo fichero, prioridad 1 mantiene primera/última pieza."""
    assert priorities_for([{"index": 0}], 0, private=True) == {0: 1}
