import sqlite3
from pathlib import Path

from catalogo.broken import clean_broken
from catalogo.db import init_db
from catalogo.strm import strm_path, version_path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeRead:
    def __init__(self, msg="Torrent not registered with this tracker"):
        self.msg = msg
        self.calls = []

    def trackers(self, info_hash):
        self.calls.append(info_hash)
        return [{"url": "http://tracker", "msg": self.msg, "status": 4}]


class FakeWrite:
    def __init__(self):
        self.deleted = []

    def delete(self, info_hash, *, delete_files=False):
        self.deleted.append((info_hash, delete_files))


def write_strm(root, asset, release_id=None):
    path = (
        strm_path(root, asset)
        if release_id is None
        else version_path(root, asset, "1080p Cast")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f"&release={release_id}" if release_id else ""
    path.write_text(
        f"http://127.0.0.1:8773/play?key={asset['key']}&download=0{suffix}\n",
        encoding="utf-8",
    )
    return path


def seed_movie(tmp_path, *, alt=False):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    root = tmp_path / "lib"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020, "SEEDING_LOCAL"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "seeders,download_url) VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000, "alive", 10,
         "http://prowlarr/1"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    if alt:
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
            "seeders,download_url) VALUES(?,?,?,?,?,?,?)",
            (asset_id, "TrackerEspanol", "Test.1080p.mkv", 2000, "alive", 5,
             "http://prowlarr/2"),
        )
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress) VALUES(?,?,?,?,?,?)",
        ("broken", release_id, "TrackerEspanol2", "seeding", "ondemand", 1),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "Test.mkv",
         str(tmp_path / "Test.mkv"), 1000, "ready"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state) VALUES(?,?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", "broken", 1000, "seeding"),
    )
    conn.commit()
    conn.close()
    asset = {
        "key": "movie:tmdb:1",
        "kind": "movie",
        "title": "Test",
        "year": 2020,
        "season": None,
        "episode": None,
    }
    default = write_strm(root, asset)
    version = write_strm(root, asset, release_id)
    return db, root, asset, default, version, release_id


def test_broken_movie_removes_torrent_and_strm(tmp_path):
    db, root, _asset, default, version, release_id = seed_movie(tmp_path)
    read, write = FakeRead(), FakeWrite()
    report = clean_broken(
        db, read, write, library_root=root, limit=5, dry_run=False
    )
    assert report["broken"]
    assert write.deleted == [("broken", True)]
    assert not default.exists()
    assert not version.exists()
    assert not default.parent.exists()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "SELECT health FROM releases WHERE id=?", (release_id,)
    ).fetchone()["health"] == "dead"
    assert conn.execute(
        "SELECT state, reason FROM seed_commitments WHERE release_id=?",
        (release_id,),
    ).fetchone()[:] == ("cancelled", "tracker_removed")
    assert conn.execute(
        "SELECT state FROM torrents WHERE info_hash='broken'"
    ).fetchone()["state"] == "removed"
    conn.close()


def test_broken_keeps_default_if_another_live_release(tmp_path):
    db, root, _asset, default, version, _release_id = seed_movie(
        tmp_path, alt=True
    )
    report = clean_broken(
        db, FakeRead(), FakeWrite(), library_root=root, limit=5, dry_run=False
    )
    assert report["broken"]
    assert default.exists()
    assert not version.exists()


def test_working_tracker_does_nothing(tmp_path):
    db, root, _asset, default, version, _release_id = seed_movie(tmp_path)
    write = FakeWrite()
    report = clean_broken(
        db, FakeRead(msg="Working"), write,
        library_root=root, limit=5, dry_run=False,
    )
    assert report["broken"] == []
    assert write.deleted == []
    assert default.exists() and version.exists()


def test_dry_run_does_not_delete(tmp_path):
    db, root, _asset, default, version, _release_id = seed_movie(tmp_path)
    write = FakeWrite()
    report = clean_broken(
        db, FakeRead(), write, library_root=root, limit=5, dry_run=True
    )
    assert report["broken"]
    assert write.deleted == []
    assert default.exists() and version.exists()


def test_pack_removed_cleans_all_episode_strm(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    root = tmp_path / "lib"
    conn = sqlite3.connect(db)
    assets = [
        {"key": "series:tmdb:2:1:1", "kind": "series", "title": "Show",
         "year": 2026, "season": 1, "episode": 1},
        {"key": "series:tmdb:2:1:2", "kind": "series", "title": "Show",
         "year": 2026, "season": 1, "episode": 2},
    ]
    release_ids = []
    asset_ids = []
    for asset in assets:
        cur = conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode,"
            "status) VALUES(?,?,?,?,?,?,?,?)",
            (asset["key"], "series", 2, "Show", 2026,
             asset["season"], asset["episode"], "SEEDING_LOCAL"),
        )
        asset_ids.append(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
            "seeders,download_url,is_pack) VALUES(?,?,?,?,?,?,?,?)",
            (asset_ids[-1], "TrackerEspanol2", "Show S01 PACK", 9000, "alive",
             10, "http://prowlarr/pack", 1),
        )
        release_ids.append(cur.lastrowid)
        conn.execute(
            "INSERT INTO seed_commitments(asset_id,release_id,tracker,"
            "size_bytes,state) VALUES(?,?,?,?,?)",
            (asset_ids[-1], release_ids[-1], "TrackerEspanol2", 9000, "seeding"),
        )
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress) VALUES(?,?,?,?,?,?)",
        ("packgone", release_ids[0], "TrackerEspanol2", "seeding", "ondemand", 1),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_ids[0], release_ids[0], torrent_id, 0, "Show S01E01.mkv",
         str(tmp_path / "Show S01E01.mkv"), 100, "ready"),
    )
    conn.commit()
    conn.close()
    paths = [write_strm(root, asset) for asset in assets]
    report = clean_broken(
        db, FakeRead(), FakeWrite(), library_root=root, limit=5, dry_run=False
    )
    assert report["broken"]
    assert all(not path.exists() for path in paths)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='alive'"
    ).fetchone()[0] == 0
    conn.close()
