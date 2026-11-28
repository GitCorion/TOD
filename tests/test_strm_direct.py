import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.jellyfin_leases import key_from_strm
from catalogo.lookup import lookup_strm
from catalogo.resolver import Resolver
from catalogo.strm import (
    direct_local_enabled,
    export_strm,
    refresh_direct_strm,
    strm_identity,
)
from catalogo import strm_inventory

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
KEY = "movie:tmdb:99"


def _seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    media = tmp_path / "media"
    media.mkdir()
    first = media / "primeira.mkv"
    second = media / "segunda.mkv"
    first.write_bytes(b"x" * 1000)
    second.write_bytes(b"y" * 2000)
    missing = media / "tercera.mkv"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        (KEY, "movie", 99, "Directa", 2024),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    releases = []
    for label, quality, path, state, storage in (
        ("Primeira.1080p.mkv", "1080p", str(first), "ready", "LOCAL_DISK"),
        ("Segunda.2160p.mkv", "4K", str(second), "archived", "1FICHIER"),
        ("Terceira.720p.mkv", "720p", str(missing), "downloading", "LOCAL_DISK"),
    ):
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,health,seeders,quality,audio) "
            "VALUES(?,?,?,?,?,?,?)",
            (asset_id, "Tracker", label, "alive", 8, quality, "castellano"),
        )
        release_id = conn.execute(
            "SELECT id FROM releases ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        releases.append(release_id)
        column = "library_path" if storage == "1FICHIER" else "local_path"
        conn.execute(
            f"INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
            f"size_bytes,state,storage_location,{column}) VALUES(?,?,?,?,?,?,?,?)",
            (asset_id, release_id, 0, label, 1000, state, storage, path),
        )
    conn.commit()
    conn.close()
    return db, releases, first, second, missing


def test_export_writes_direct_path_for_main_and_available_versions(tmp_path):
    db, releases, first, second, _ = _seed(tmp_path)
    out = tmp_path / "strm"
    result = export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        versions_filter="alive", direct_local=True,
    )
    assert result["direct_local"] is True
    assert result["written"] == 1
    assert result["versions_written"] == 3
    folder = out / "movies" / "Directa (2024)"
    main = folder / "Directa (2024).strm"
    assert main.read_text(encoding="utf-8").strip() == str(first)
    direct_versions = [
        path for path in folder.glob("Directa (2024) - *.strm")
        if not path.read_text(encoding="utf-8").startswith("http")
    ]
    assert len(direct_versions) == 2
    assert {p.read_text(encoding="utf-8").strip() for p in direct_versions} == {
        str(first), str(second)
    }
    proxy_versions = [
        path for path in folder.glob("Directa (2024) - *.strm")
        if path.read_text(encoding="utf-8").startswith("http")
    ]
    assert len(proxy_versions) == 1
    assert f"&release={releases[2]}" in proxy_versions[0].read_text()


def test_export_falls_back_to_proxy_when_file_is_missing(tmp_path):
    db, _, first, second, _ = _seed(tmp_path)
    first.unlink()
    second.unlink()
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        versions_filter="alive", direct_local=True,
    )
    main = out / "movies" / "Directa (2024)" / "Directa (2024).strm"
    assert main.read_text(encoding="utf-8").strip() == (
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A99&download=0"
    )


def test_strm_identity_maps_direct_path_back_to_asset(tmp_path):
    db, releases, first, _, _ = _seed(tmp_path)
    path = tmp_path / "Directa.strm"
    path.write_text(f"{first}\n", encoding="utf-8")
    assert strm_identity(db, path) == (KEY, releases[0])


def test_inventory_counts_direct_strm_as_published(tmp_path):
    db, _, first, _, _ = _seed(tmp_path)
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        versions_filter="alive", direct_local=True,
    )
    strm_inventory.invalidate(out)
    inv = strm_inventory.inventory(out, db_path=db)
    assert inv["movies"] == 1
    assert inv["versions"] == 3
    assert KEY in inv["keys"]
    assert str(out / "movies" / "Directa (2024)" / "Directa (2024).strm") in inv["paths"]


def test_leases_and_lookup_understand_direct_strm(tmp_path):
    db, _, first, _, _ = _seed(tmp_path)
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        versions_filter="alive", direct_local=True,
    )
    main = out / "movies" / "Directa (2024)" / "Directa (2024).strm"
    assert key_from_strm(str(main), db_path=db) == KEY
    result = lookup_strm(str(db), str(main), allowed_roots=(str(out),))
    assert result["is_catalog"] is True
    assert result["key"] == KEY
    assert result["play_url"].startswith("/play?key=")


def test_refresh_direct_strm_rewrites_changed_path(tmp_path):
    db, _, first, second, _ = _seed(tmp_path)
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        versions_filter="alive", direct_local=True,
    )
    main = out / "movies" / "Directa (2024)" / "Directa (2024).strm"
    assert main.read_text(encoding="utf-8").strip() == str(first)

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE torrent_files SET local_path=? WHERE local_path=?",
        (str(second), str(first)),
    )
    conn.commit()
    conn.close()

    report = refresh_direct_strm(db, out, [KEY], direct_local=True)
    assert report["enabled"] is True
    assert report["refreshed"] == [KEY]
    assert main.read_text(encoding="utf-8").strip() == str(second)


def test_direct_local_enabled_reads_db_override(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    assert direct_local_enabled(db) is False
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
        ("strm_direct_local", "1", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    assert direct_local_enabled(db) is True
