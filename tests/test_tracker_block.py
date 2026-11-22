import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.policy import TrackerPolicy, upsert_policy
from catalogo.tracker_block import set_tracker_blocked

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    for title in ("a.mkv", "b.mkv"):
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,seeders) "
            "VALUES(?,?,?,?,?,?)",
            (asset_id, "TrackerJackett (API)", title, 1000, "alive", 5),
        )
    conn.commit()
    conn.close()
    upsert_policy(str(db), TrackerPolicy("TrackerJackett (API)", min_seed_hours=120))
    return db


def test_block_and_unblock_tracker(tmp_path):
    db = seed(tmp_path)
    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=True)
    assert report["releases"] == 2
    assert report["policy"] == "disabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='blocked'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 0
    conn.close()

    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=False)
    assert report["releases"] == 2
    assert report["policy"] == "enabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='unknown'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 1
    conn.close()


def test_block_dry_run_changes_nothing(tmp_path):
    db = seed(tmp_path)
    report = set_tracker_blocked(
        str(db), "TrackerJackett (API)", blocked=True, dry_run=True
    )
    assert report["releases"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM releases WHERE health='blocked'"
    ).fetchone()[0] == 0
    conn.close()


def _strm_setup(tmp_path, *, other_source=True, local_file=False):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerA", "a.mkv", "alive", 5),
    )
    release_a = conn.execute("SELECT id FROM releases").fetchone()[0]
    if other_source:
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
            "VALUES(?,?,?,?,?)",
            (asset_id, "TrackerB", "b.mkv", "alive", 5),
        )
    if local_file:
        conn.execute(
            "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
            "size_bytes,state,storage_location,local_path) VALUES(?,?,?,?,?,?,?,?)",
            (asset_id, release_a, 0, "a.mkv", 1000, "ready", "LOCAL_DISK",
             str(tmp_path / "a.mkv")),
        )
    conn.commit()
    conn.close()

    out = tmp_path / "strm"
    title_dir = out / "movies" / "Test (2020)"
    title_dir.mkdir(parents=True)
    main = title_dir / "Test (2020).strm"
    main.write_text("http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1\n")
    variant = title_dir / "Test (2020) - 1080p.strm"
    variant.write_text(
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1"
        f"&release={release_a}\n"
    )
    return db, out, main, variant


def test_delete_tracker_strm_keeps_main_with_other_source(tmp_path):
    from catalogo.tracker_block import delete_tracker_strm

    db, out, main, variant = _strm_setup(tmp_path, other_source=True)
    report = delete_tracker_strm(str(db), out, "TrackerA")
    assert report["variants"] == 1
    assert report["main"] == 0
    assert report["kept"] == 1
    assert not variant.exists()
    assert main.exists()


def test_delete_tracker_strm_removes_main_without_other_source(tmp_path):
    from catalogo.tracker_block import delete_tracker_strm

    db, out, main, variant = _strm_setup(tmp_path, other_source=False)
    report = delete_tracker_strm(str(db), out, "TrackerA")
    assert report["variants"] == 1
    assert report["main"] == 1
    assert report["total"] == 2
    assert not variant.exists()
    assert not main.exists()
    assert not (out / "movies" / "Test (2020)").exists()


def test_delete_tracker_strm_keeps_main_with_local_file(tmp_path):
    from catalogo.tracker_block import delete_tracker_strm

    db, out, main, variant = _strm_setup(
        tmp_path, other_source=False, local_file=True
    )
    report = delete_tracker_strm(str(db), out, "TrackerA")
    assert report["variants"] == 1
    assert report["main"] == 0
    assert report["kept"] == 1
    assert main.exists()


def test_delete_tracker_strm_dry_run_keeps_files(tmp_path):
    from catalogo.tracker_block import delete_tracker_strm

    db, out, main, variant = _strm_setup(tmp_path, other_source=False)
    report = delete_tracker_strm(str(db), out, "TrackerA", dry_run=True)
    assert report["total"] == 2
    assert variant.exists()
    assert main.exists()


def test_block_creates_missing_policy(tmp_path):
    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM tracker_policies")
    conn.commit()
    conn.close()

    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=True)
    assert report["policy"] == "disabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 0
    conn.close()

    report = set_tracker_blocked(str(db), "TrackerJackett (API)", blocked=False)
    assert report["policy"] == "enabled"
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT enabled FROM tracker_policies WHERE tracker='TrackerJackett (API)'"
    ).fetchone()[0] == 1
    conn.close()


def test_list_trackers_marks_blocked_releases_without_policy(tmp_path):
    from catalogo.queries import list_trackers

    db = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM tracker_policies")
    conn.execute("UPDATE releases SET health='blocked'")
    conn.commit()
    conn.close()

    rows = list_trackers(db)
    assert rows[0]["tracker"] == "TrackerJackett (API)"
    assert rows[0]["blocked"] == 2
    assert rows[0]["enabled"] == 0
