import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.qbit_sync import _storage_for, radarr_index, sonarr_index, sync

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
EPOCH = 1794000000


class FakeQbit:
    def __init__(self):
        self.files_calls = []
        self._torrents = [
            {
                "hash": "cat1", "category": "catalogo", "state": "stalledUP",
                "progress": 1, "save_path": "/mnt/descargas/catalogo",
                "content_path": "/mnt/descargas/catalogo/Test.mkv",
                "download_path": "/mnt/cache/tmp/catalogo",
                "completion_on": EPOCH, "seeding_time": 100,
                "added_on": EPOCH - 1000, "uploaded": 10, "downloaded": 1000,
                "ratio": 0.01, "size": 1000, "tracker": "TrackerEspanol2",
            },
            {
                "hash": "rad1", "category": "radarr", "state": "queuedDL",
                "progress": 0, "save_path": "/mnt/descargas/radarr",
                "content_path": "/mnt/cache/tmp/radarr/Test2.mkv",
                "download_path": "/mnt/cache/tmp/radarr",
                "completion_on": -1, "seeding_time": 0,
                "added_on": EPOCH - 100, "uploaded": 0, "downloaded": 0,
                "ratio": 0, "size": 2000, "tracker": "TrackerEspanol2",
            },
            {
                "hash": "comp1", "category": "completo", "state": "stalledUP",
                "progress": 1, "save_path": "/mnt/library/archive",
                "content_path": "/mnt/library/archive/Comp.mkv",
                "download_path": "/mnt/cache/tmp/completo",
                "completion_on": EPOCH, "seeding_time": 5000,
                "added_on": EPOCH - 5000, "uploaded": 0, "downloaded": 3000,
                "ratio": 0, "size": 3000, "tracker": "TrackerEspanol2",
            },
        ]

    def torrents(self, category=None):
        return self._torrents

    def files(self, info_hash):
        self.files_calls.append(info_hash)
        if info_hash == "cat1":
            return [{"index": 0, "name": "Test.mkv", "size": 1000,
                     "progress": 1, "priority": 1}]
        if info_hash == "comp1":
            return [{"index": 0, "name": "Comp.mkv", "size": 3000,
                     "progress": 1, "priority": 1}]
        return []


class FakeRadarrAPI:
    def movies(self):
        return [
            {"id": 1, "tmdbId": 100, "title": "Comp Movie", "year": 2024}
        ]

    def history_all(self, **_kwargs):
        return [{"downloadId": "COMP1", "movieId": 1}]


class FakeSonarrAPI:
    def series(self):
        return [
            {
                "id": 1,
                "tvdbId": 555,
                "tmdbId": 200,
                "title": "Test Show",
            }
        ]

    def episodes(self, series_id):
        return [
            {
                "id": 1,
                "seasonNumber": 1,
                "episodeNumber": 2,
            }
        ]

    def history_all(self, **_kwargs):
        return [{"downloadId": "SER1", "seriesId": 1, "episodeId": 1}]


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key, media_id, kind, tmdb, title, year) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes) VALUES(?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash, release_id, state) VALUES(?,?,?)",
        ("cat1", release_id, "seeding"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id, release_id, torrent_id, file_index, "
        "local_path, size_bytes, state, storage_location) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0,
         "/mnt/descargas/catalogo/Test.mkv", 1000, "ready", "LOCAL_DISK"),
    )
    conn.commit()
    conn.close()
    return db, asset_id


def test_storage_accepts_separate_archive_roots():
    roots = ("/mnt/fichier/Peliculas", "/mnt/fichier/Series")
    assert _storage_for("/mnt/fichier/Peliculas/x.mkv", roots) == "1FICHIER"
    assert _storage_for("/mnt/fichier/Series/x.mkv", roots) == "1FICHIER"
    assert _storage_for("/mnt/descargas/ondemand/x.mkv", roots) == "LOCAL_DISK"
    assert _storage_for("/otro/x.mkv", roots) == "NONE"


def test_indexes(tmp_path):
    radarr = FakeRadarrAPI()
    sonarr = FakeSonarrAPI()
    assert radarr_index(radarr)["comp1"]["tmdb"] == 100
    assert sonarr_index(sonarr)["ser1"][0]["tmdb"] == 200
    assert sonarr_index(sonarr)["ser1"][0]["tvdb"] == 555


class LockProbeQbit(FakeQbit):
    """Comprueba que `files()` se llama sin una transacción de escritura abierta."""

    db = None

    def files(self, info_hash):
        conn = sqlite3.connect(self.db, timeout=0.2)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        finally:
            conn.close()
        return super().files(info_hash)


def test_sync_does_not_hold_write_lock_during_qbit_calls(tmp_path):
    db, _ = seed_db(tmp_path)
    client = LockProbeQbit()
    client.db = db
    report = sync(
        db, client, radarr=FakeRadarrAPI(), sonarr=FakeSonarrAPI(),
        dry_run=False, check_exists=False, now=NOW,
    )
    assert report.errors == []
    assert set(client.files_calls) == {"cat1", "comp1"}


def test_sync_read_only_and_dedup(tmp_path):
    db, asset_id = seed_db(tmp_path)
    radarr = FakeRadarrAPI()
    sonarr = FakeSonarrAPI()
    client = FakeQbit()

    report = sync(
        db, client, radarr=radarr, sonarr=sonarr,
        dry_run=False, check_exists=False, now=NOW,
    )
    assert report.torrents_seen == 3
    assert report.files_fetched == 2
    assert "rad1" not in client.files_calls
    assert set(client.files_calls) == {"cat1", "comp1"}

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    torrents = {
        row["info_hash"]: row for row in conn.execute("SELECT * FROM torrents")
    }
    assert torrents["comp1"]["category"] == "completo"
    assert torrents["comp1"]["progress"] == 1
    assert torrents["rad1"]["category"] == "radarr"
    assert torrents["rad1"]["progress"] == 0

    media = conn.execute(
        "SELECT * FROM media_items WHERE key='movie:tmdb:100'"
    ).fetchone()
    assert media["status"] == "ON_1FICHIER"
    assert media["active_file_id"] is not None

    cat_media = conn.execute(
        "SELECT * FROM media_items WHERE id=?", (asset_id,)
    ).fetchone()
    assert cat_media["status"] == "SEEDING_LOCAL"

    files = conn.execute(
        "SELECT * FROM torrent_files WHERE asset_id=?", (media["id"],)
    ).fetchall()
    assert len(files) == 1
    assert files[0]["storage_location"] == "1FICHIER"
    assert files[0]["cloud_uploaded_at"] is not None
    conn.close()


def test_sync_is_idempotent_and_dry_run(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    radarr = FakeRadarrAPI()
    sonarr = FakeSonarrAPI()
    client = FakeQbit()

    first = sync(db, client, radarr=radarr, sonarr=sonarr,
                 check_exists=False, now=NOW)
    second = sync(db, client, radarr=radarr, sonarr=sonarr,
                  check_exists=False, now=NOW)
    assert first.torrents_seen == second.torrents_seen == 3
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM media_items WHERE key='movie:tmdb:100'").fetchone()[0] == 1
    conn.close()

    db2, _ = seed_db(tmp_path / "dry")
    dry = sync(db2, client, radarr=radarr, sonarr=sonarr,
               dry_run=True, check_exists=False, now=NOW)
    assert dry.dry_run
    conn = sqlite3.connect(db2)
    assert conn.execute("SELECT COUNT(*) FROM media_items WHERE key='movie:tmdb:100'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM torrents WHERE info_hash='comp1'").fetchone()[0] == 0
    conn.close()


def test_qbittorrent5_stopped_states_are_paused():
    from catalogo.qbit_sync import _qbit_state_to_db

    assert _qbit_state_to_db("stoppedDL", 0) == "paused"
    assert _qbit_state_to_db("stoppedUP", 1) == "paused"
    assert _qbit_state_to_db("pausedDL", 0.5) == "paused"


def seed_removed_commitment(tmp_path, started_at):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tracker_policies(tracker,privacy,min_seed_hours,"
        "target_ratio,enabled) VALUES(?,?,?,?,?)",
        ("TrackerEspanol2", "private", 120, 0.0, 1),
    )
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?)",
        ("movie:tmdb:9", "movie", 9, "Borrada", 2026, "SEEDING_LOCAL"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Borrada.mkv", 1000, "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress) VALUES(?,?,?,?,?,?)",
        ("gone", release_id, "TrackerEspanol2", "seeding", "ondemand", 1),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,"
        "rel_path,local_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, "Borrada.mkv",
         "/mnt/descargas/ondemand/Borrada.mkv", 1000, "ready"),
    )
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,info_hash,"
        "size_bytes,state,started_at) VALUES(?,?,?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", "gone", 1000, "seeding",
         started_at.isoformat()),
    )
    conn.commit()
    conn.close()
    return db, asset_id, release_id


class EmptyQbit:
    def torrents(self, category=None):
        return []

    def files(self, info_hash):
        return []


def test_ondemand_removed_after_min_seed_is_closed(tmp_path):
    db, asset_id, release_id = seed_removed_commitment(
        tmp_path, NOW - timedelta(hours=200)
    )
    report = sync(
        db, EmptyQbit(), 
        check_exists=False, now=NOW,
    )
    assert report.torrents_removed == 1
    assert report.reseed_required == 0
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "SELECT state FROM seed_commitments WHERE release_id=?",
        (release_id,),
    ).fetchone()["state"] == "done"
    assert conn.execute(
        "SELECT status FROM media_items WHERE id=?", (asset_id,)
    ).fetchone()["status"] == "COLD"
    conn.close()


def test_ondemand_removed_early_is_left_to_qbittorrent(tmp_path):
    db, asset_id, release_id = seed_removed_commitment(
        tmp_path, NOW - timedelta(hours=1)
    )
    report = sync(
        db, EmptyQbit(), 
        check_exists=False, now=NOW,
    )
    assert report.torrents_removed == 1
    assert report.reseed_required == 0
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT state, reason FROM seed_commitments WHERE release_id=?",
        (release_id,),
    ).fetchone()
    assert row["state"] == "done"
    assert row["reason"] == "qbit_removed"
    assert conn.execute(
        "SELECT status FROM media_items WHERE id=?", (asset_id,)
    ).fetchone()["status"] == "COLD"
    conn.close()


def test_sync_trusts_archive_without_stat(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    report = sync(
        db, FakeQbit(), radarr=FakeRadarrAPI(), sonarr=FakeSonarrAPI(),
        archive_root="/mnt/library", dry_run=False, check_exists=True, now=NOW,
    )
    assert report.errors == []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT f.* FROM torrent_files f JOIN torrents t ON t.id=f.torrent_id "
        "WHERE t.info_hash='comp1'"
    ).fetchone()
    assert row["storage_location"] == "1FICHIER"
    assert row["library_path"] == "/mnt/library/archive/Comp.mkv"
    assert row["local_path"] is None
    assert row["state"] == "archived"
    conn.close()


def test_normalize_storage_reclassifies_old_archive_rows(tmp_path):
    from catalogo.qbit_sync import _normalize_storage

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    file_id = conn.execute(
        "INSERT INTO torrent_files(local_path, storage_location, state) "
        "VALUES(?,?,?)",
        ("/mnt/library/archive/Test.mkv", "NONE", "ready"),
    ).lastrowid
    _normalize_storage(conn, "/mnt/library")
    row = conn.execute(
        "SELECT * FROM torrent_files WHERE id=?", (file_id,)
    ).fetchone()
    assert row["local_path"] is None
    assert row["library_path"] == "/mnt/library/archive/Test.mkv"
    assert row["storage_location"] == "1FICHIER"
    conn.close()


def test_choose_active_prefers_healthy_spanish_over_larger_blocked(tmp_path):
    from catalogo.qbit_sync import _choose_active

    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,season,episode,title,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("series:tmdb:9:1:1", "series", 9, 1, 1, "Test", "COLD"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    blocked = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,audio) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerInternacional", "ep.mkv", 5000, "blocked", "unknown"),
    ).lastrowid
    spanish = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,audio) "
        "VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "pack", 4000, "alive", "castellano"),
    ).lastrowid
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
        "size_bytes,state,library_path) VALUES(?,?,?,?,?,?,?)",
        (asset_id, blocked, 0, "ep.mkv", 5000, "archived",
         "/mnt/library/archive/ep.mkv"),
    )
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
        "size_bytes,state,local_path) VALUES(?,?,?,?,?,?,?)",
        (asset_id, spanish, 0, "pack/ep.mkv", 4000, "ready",
         "/mnt/descargas/ondemand/pack/ep.mkv"),
    )
    conn.commit()
    good = conn.execute(
        "SELECT id FROM torrent_files WHERE release_id=?", (spanish,)
    ).fetchone()[0]
    status, active = _choose_active(conn, asset_id)
    conn.close()
    assert active == good
    assert status == "SEEDING_LOCAL"


class QueuedArrQbit(FakeQbit):
    def __init__(self):
        super().__init__()
        self._torrents.append(
            {
                "hash": "arr1", "category": "radarr", "state": "queuedDL",
                "progress": 0, "save_path": "/mnt/descargas/radarr",
                "content_path": "/mnt/descargas/radarr/Arr.Movie.mkv",
                "download_path": "/mnt/cache/tmp/radarr",
                "completion_on": -1, "seeding_time": 0,
                "added_on": EPOCH - 50, "uploaded": 0, "downloaded": 0,
                "ratio": 0, "size": 2000, "tracker": "TrackerEspanol2",
            }
        )

    def files(self, info_hash):
        if info_hash == "arr1":
            self.files_calls.append(info_hash)
            return [
                {"index": 0, "name": "Arr.Movie.mkv", "size": 2000,
                 "progress": 0, "priority": 1}
            ]
        return super().files(info_hash)


class QueuedArrRadarr(FakeRadarrAPI):
    def movies(self):
        return super().movies() + [
            {"id": 2, "tmdbId": 300, "title": "Arr Movie", "year": 2025}
        ]

    def history_all(self, **kwargs):
        return super().history_all(**kwargs) + [
            {"downloadId": "ARR1", "movieId": 2}
        ]


def test_queued_arr_movie_is_mapped_even_at_zero(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    client = QueuedArrQbit()
    report = sync(
        db, client, radarr=QueuedArrRadarr(), sonarr=FakeSonarrAPI(),
        dry_run=False, check_exists=False, now=NOW,
    )
    assert "arr1" in client.files_calls
    assert "movie:tmdb:300" in report.arr_keys
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    media = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:300'"
    ).fetchone()
    assert media is not None
    mapped = conn.execute(
        "SELECT f.asset_id, f.state FROM torrent_files f "
        "JOIN torrents t ON t.id=f.torrent_id WHERE t.info_hash='arr1'"
    ).fetchone()
    conn.close()
    assert mapped["asset_id"] == media["id"]
    assert mapped["state"] == "unknown"


class DoneArrQbit(FakeQbit):
    def __init__(self):
        super().__init__()
        self._torrents.append(
            {
                "hash": "arr2", "category": "radarr", "state": "stalledUP",
                "progress": 1, "save_path": "/mnt/descargas/radarr",
                "content_path": "/mnt/descargas/radarr/Done.Movie.mkv",
                "download_path": "/mnt/cache/tmp/radarr",
                "completion_on": EPOCH, "seeding_time": 100,
                "added_on": EPOCH - 100, "uploaded": 0, "downloaded": 1000,
                "ratio": 0, "size": 1000, "tracker": "TrackerEspanol2",
            }
        )

    def files(self, info_hash):
        if info_hash == "arr2":
            self.files_calls.append(info_hash)
            return [
                {"index": 0, "name": "Done.Movie.mkv", "size": 1000,
                 "progress": 1, "priority": 1}
            ]
        return super().files(info_hash)


class DoneArrRadarr(QueuedArrRadarr):
    def movies(self):
        return super().movies() + [
            {"id": 3, "tmdbId": 400, "title": "Done Movie", "year": 2025}
        ]

    def history_all(self, **kwargs):
        return super().history_all(**kwargs) + [
            {"downloadId": "ARR2", "movieId": 3}
        ]


def test_completed_arr_torrent_is_reported_for_prune(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    report = sync(
        db, DoneArrQbit(), radarr=DoneArrRadarr(), sonarr=FakeSonarrAPI(),
        dry_run=False, check_exists=False, now=NOW,
    )
    assert "movie:tmdb:400" in report.arr_done_keys
    assert "movie:tmdb:400" not in report.arr_keys


class PartialPackQbit(FakeQbit):
    def __init__(self):
        super().__init__()
        self._torrents.append(
            {
                "hash": "pack1", "category": "ondemand", "state": "downloading",
                "progress": 0.4, "save_path": "/mnt/descargas/ondemand",
                "download_path": "/mnt/cache/tmp/ondemand",
                "content_path": "/mnt/descargas/ondemand/Pack",
                "completion_on": -1, "seeding_time": 0,
                "added_on": EPOCH - 50, "uploaded": 0, "downloaded": 400,
                "ratio": 0, "size": 1000, "tracker": "TrackerEspanol2",
            }
        )

    def files(self, info_hash):
        if info_hash == "pack1":
            self.files_calls.append(info_hash)
            return [
                {"index": 0, "name": "Pack/E01.mkv", "size": 500,
                 "progress": 1, "priority": 6},
                {"index": 1, "name": "Pack/E02.mkv", "size": 500,
                 "progress": 0, "priority": 0},
            ]
        return super().files(info_hash)


def test_complete_file_in_incomplete_pack_is_marked_ready(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    report = sync(
        db, PartialPackQbit(), radarr=FakeRadarrAPI(), sonarr=FakeSonarrAPI(),
        dry_run=False, check_exists=False, now=NOW,
    )
    assert report.torrents_written >= 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT f.state, f.local_path FROM torrent_files f "
        "JOIN torrents t ON t.id=f.torrent_id WHERE t.info_hash='pack1' "
        "ORDER BY f.file_index"
    ).fetchall()
    conn.close()
    assert rows[0]["state"] == "ready"
    assert rows[0]["local_path"]
    assert rows[1]["state"] == "unknown"


class StablePackQbit:
    """Torrent ya indexado: sync no vuelve a pedir la lista de ficheros."""

    def __init__(self, torrent):
        self._torrent = torrent
        self.files_calls = []

    def torrents(self, category=None):
        return [self._torrent]

    def files(self, info_hash):
        self.files_calls.append(info_hash)
        return []


def test_pack_files_are_linked_to_their_local_episodes(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    episode_ids = {}
    for number in (1, 2, 3):
        key = f"series:tmdb:300:1:{number}"
        conn.execute(
            "INSERT INTO media_items(key, media_id, kind, tmdb, tvdb_id, "
            "title, season, episode) VALUES(?,?,?,?,?,?,?,?)",
            (key, key, "series", 300, 900, "Serie", 1, number),
        )
        episode_ids[number] = conn.execute(
            "SELECT id FROM media_items WHERE key=?", (key,)
        ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,is_pack) "
        "VALUES(?,?,?,?,?)",
        (episode_ids[1], "Tracker", "Serie S01 PACK", 30, 1),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,"
        "progress,save_path,qbit_state,content_path) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("packrelink", release_id, "Tracker", "seeding", "ondemand", 1,
         "/mnt/descargas/ondemand", "stalledUP",
         "/mnt/descargas/ondemand/Serie S01 PACK"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    for number in (1, 2, 3):
        rel_path = f"Serie S01 PACK/Serie S01E0{number}.mkv"
        conn.execute(
            "INSERT INTO torrent_files(asset_id,release_id,torrent_id,"
            "file_index,rel_path,local_path,size_bytes,state,storage_location) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (episode_ids[number] if number == 1 else None, release_id,
             torrent_id, number - 1, rel_path,
             f"/mnt/descargas/ondemand/{rel_path}", 10, "ready",
             "LOCAL_DISK"),
        )
    conn.commit()
    conn.close()
    qbit = StablePackQbit(
        {
            "hash": "packrelink", "category": "ondemand", "state": "stalledUP",
            "progress": 1, "save_path": "/mnt/descargas/ondemand",
            "content_path": "/mnt/descargas/ondemand/Serie S01 PACK",
            "download_path": "/mnt/descargas/ondemand",
            "completion_on": EPOCH, "seeding_time": 100,
            "added_on": EPOCH - 1000, "uploaded": 10, "downloaded": 30,
            "ratio": 0.3, "size": 30, "tracker": "Tracker",
        }
    )

    report = sync(db, qbit, dry_run=False, check_exists=False, now=NOW)

    assert qbit.files_calls == []
    assert report.media_linked >= 2
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT asset_id FROM torrent_files WHERE torrent_id=? "
        "ORDER BY file_index",
        (torrent_id,),
    ).fetchall()
    statuses = {
        int(row["episode"]): row["status"]
        for row in conn.execute(
            "SELECT episode, status FROM media_items WHERE tmdb=300"
        )
    }
    conn.close()
    assert [row["asset_id"] for row in rows] == [
        episode_ids[1], episode_ids[2], episode_ids[3]
    ]
    assert statuses[2] == "SEEDING_LOCAL"
    assert statuses[3] == "SEEDING_LOCAL"


def test_ready_ondemand_pack_queues_strm_publish(tmp_path):
    db, _asset_id = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    for ep in (1, 2):
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
            "VALUES(?,?,?,?,?,?,?)",
            (f"series:tmdb:200:1:{ep}", "series", 200, "Pack", 2026, 1, ep),
        )
    ep1_id = conn.execute(
        "SELECT id FROM media_items WHERE key='series:tmdb:200:1:1'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,guid,title,size_bytes,is_pack) "
        "VALUES(?,?,?,?,?,1)",
        (ep1_id, "TrackerEspanol2", "pack1-guid", "Pack S01", 1000),
    )
    rel_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category) "
        "VALUES('pack1',?,'TrackerEspanol2','downloading','ondemand')",
        (rel_id,),
    )
    conn.commit()
    conn.close()

    class NamedPackQbit(PartialPackQbit):
        def files(self, info_hash):
            if info_hash == "pack1":
                self.files_calls.append(info_hash)
                return [
                    {"index": 0, "name": "Pack/S01E01.mkv", "size": 500,
                     "progress": 1, "priority": 6},
                    {"index": 1, "name": "Pack/S01E02.mkv", "size": 500,
                     "progress": 0, "priority": 0},
                ]
            return super().files(info_hash)

    qbit = NamedPackQbit()
    report = sync(db, qbit, dry_run=False, check_exists=False, now=NOW)
    assert "series:tmdb:200:1:1" in report.arr_keys
    assert "series:tmdb:200:1:2" not in report.arr_keys
    assert "series:tmdb:200:1:1" not in report.arr_done_keys
