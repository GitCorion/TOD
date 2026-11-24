import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.packs import prune_misassigned_packs

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for season, episode in ((1, 1), (2, 1)):
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,season,episode) "
            "VALUES(?,?,?,?,?,?)",
            (
                f"series:tmdb:1:{season}:{episode}",
                "series",
                1,
                "Serie",
                season,
                episode,
            ),
        )
    s01 = conn.execute(
        "SELECT id FROM media_items WHERE season=1"
    ).fetchone()[0]
    s02 = conn.execute(
        "SELECT id FROM media_items WHERE season=2"
    ).fetchone()[0]
    for title, asset, pack in (
        ("Serie S01 [PACK]", s01, 1),
        ("Serie S02 [PACK]", s01, 1),
        ("Serie S02 [PACK]", s02, 1),
        ("Serie S01E01 1080p", s01, 1),
        ("Serie S02 [PACK] referenciado", s01, 1),
    ):
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,is_pack) "
            "VALUES(?,?,?,?)",
            (asset, "Tracker", title, pack),
        )
    referenced = conn.execute(
        "SELECT id FROM releases WHERE title LIKE '%referenciado'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id) VALUES(?,?)",
        ("hash", referenced),
    )
    conn.commit()
    conn.close()
    return db, s01


def test_prune_misassigned_packs(tmp_path):
    db, s01 = seed(tmp_path)

    report = prune_misassigned_packs(db, dry_run=True)
    assert report["delete"] == 1
    assert report["flag_fixed"] == 1
    assert len(report["referenced"]) == 1

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 5
    conn.close()

    report = prune_misassigned_packs(db, dry_run=False)
    assert report["delete"] == 1
    assert report["flag_fixed"] == 1

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT title, is_pack FROM releases ORDER BY id"
    ).fetchall()
    conn.close()
    assert ("Serie S01 [PACK]", 1) in rows
    assert ("Serie S02 [PACK]", 1) in rows
    assert ("Serie S01E01 1080p", 0) in rows
    assert ("Serie S02 [PACK] referenciado", 1) in rows
    assert ("Serie S02 [PACK]", 1) in rows
    assert len(rows) == 4
