"""Tests de Fase 4: Tags, Custom Filters, Import Lists, Series Presets, Mass Edit, Blocklist, Cutoff y Calendar."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catalogo import lists, ondemand, profiles, queries
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "tod.db"
    lib = tmp_path / "library"
    lib.mkdir()
    init_db(db, MIGRATIONS)
    cfg = Config(
        db_path=db,
        legacy_db=db,
        library_root=tmp_path / "arr_lib",
        seed_root=tmp_path / "descargas",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:1/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1",
        qbittorrent_user="x",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:1/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=lib,
        max_active_ondemand=4,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO media_items(key, kind, tmdb, title, year, genres, release_date, monitored, popularity)
        VALUES ('movie:tmdb:100', 'movie', 100, 'Pelicula Accion', 2024, 'Action, Sci-Fi', '2026-12-01', 1, 50)
        """
    )
    m_id = conn.execute("SELECT id FROM media_items WHERE key='movie:tmdb:100'").fetchone()[0]
    conn.execute(
        """
        INSERT INTO releases(asset_id, tracker, title, quality, audio, source_type, size_bytes, seeders, health, privacy)
        VALUES (?, 'HDO', 'Pelicula.Accion.2024.720p.HDTV.ESP', '720p', 'castellano', 'hdtv', 1000000000, 12, 'alive', 'private')
        """,
        (m_id,),
    )
    # Serie con 2 temporadas y 2 episodios cada una
    for s in (1, 2):
        for e in (1, 2):
            air = "2024-01-01" if s == 1 else "2099-05-01"
            conn.execute(
                """
                INSERT INTO media_items(key, kind, tmdb, title, year, season, episode, air_date, genres, series_status, monitored, popularity)
                VALUES (?, 'series', 200, 'Mi Serie', 2024, ?, ?, ?, 'Drama', 'Returning Series', 1, 80)
                """,
                (f"series:tmdb:200:{s}:{e}", s, e, air),
            )
    s1e1 = conn.execute("SELECT id FROM media_items WHERE key='series:tmdb:200:1:1'").fetchone()[0]
    conn.execute(
        """
        INSERT INTO releases(asset_id, tracker, title, quality, audio, source_type, size_bytes, seeders, health, privacy)
        VALUES (?, 'DivxTotal', 'Mi Serie S01E01 1080p', '1080p', 'castellano', 'webdl', 800000000, 5, 'alive', 'public')
        """,
        (s1e1,),
    )
    conn.commit()
    conn.close()
    ondemand.invalidate_caches()
    return db, cfg


def test_tags_and_custom_filters_with_catalog(env):
    db, cfg = env
    t1 = lists.save_tag(db, "4K-Favoritas", "#ff0000")
    assert t1["name"] == "4k-favoritas"
    lists.set_media_tags(db, "movie", 100, [t1["id"]])

    cf = lists.save_custom_filter(db, {
        "name": "Privados Accion",
        "target": "catalog",
        "filters": {"privacy": "private", "genre": "Action", "tag": "4k-favoritas"},
    })
    assert cf["filters"]["privacy"] == "private"

    ondemand.invalidate_caches()
    res = ondemand.catalog(db, cfg, group="series", custom_filter_id=cf["id"])
    assert len(res["items"]) == 1
    assert res["items"][0]["tmdb"] == 100
    assert res["items"][0]["tags"][0]["name"] == "4k-favoritas"

    # Filtro negativo por tracker público
    res2 = ondemand.catalog(db, cfg, group="series", privacy="public", quality="1080p")
    assert [i["tmdb"] for i in res2["items"]] == [200]

    assert lists.delete_custom_filter(db, cf["id"])
    assert lists.delete_tag(db, t1["id"])


def test_series_monitor_presets_and_settings(env):
    db, cfg = env
    # Pilot -> solo S01E01
    r_pilot = lists.apply_series_monitor_preset(db, 200, "pilot")
    assert r_pilot["monitored"] == 1
    # Future -> solo temporada 2 (air_date 2099-05-01)
    r_fut = lists.apply_series_monitor_preset(db, 200, "future")
    assert r_fut["monitored"] == 2
    # Latest season -> temporada 2
    r_last = lists.apply_series_monitor_preset(db, 200, "latest_season")
    assert r_last["monitored"] == 2
    # None -> 0
    r_none = lists.apply_series_monitor_preset(db, 200, "none")
    assert r_none["monitored"] == 0

    # Save series settings -> anime actualiza is_anime y series_type
    ss = lists.save_series_settings(db, 200, {"series_type": "anime", "monitor_mode": "all"})
    assert ss["series_type"] == "anime"
    conn = sqlite3.connect(db)
    is_anime = conn.execute("SELECT is_anime FROM media_items WHERE tmdb=200 LIMIT 1").fetchone()[0]
    conn.close()
    assert is_anime == 1


def test_mass_edit_and_reorganize(env):
    db, cfg = env
    p = profiles.save_profile(db, {"name": "Perfil Test", "qualities": ["1080p", "720p"], "cutoff_quality": "1080p"})
    t = lists.save_tag(db, "lote")
    res = lists.mass_edit_media(
        db,
        ["movie:tmdb:100", "series:200"],
        quality_profile_id=p["id"],
        monitored=False,
        add_tag_ids=[t["id"]],
    )
    assert res["movies"] == 1
    assert res["series"] == 1
    ondemand.invalidate_caches()
    cat = ondemand.catalog(db, cfg, group="series", profile_id=p["id"], tag="lote")
    assert len(cat["items"]) == 2
    assert all(not i["monitored"] for i in cat["items"])


def test_blocklist_and_cutoff_unmet_virtual(env):
    db, cfg = env
    conn = sqlite3.connect(db)
    rel_id = conn.execute("SELECT id FROM releases WHERE tracker='HDO'").fetchone()[0]
    conn.close()

    # Pelicula Accion tiene 720p; perfil default cutoff es 1080p -> cutoff_unmet!
    cu = lists.list_cutoff_unmet(db, kind="movie")
    assert any(i["tmdb"] == 100 for i in cu["items"])

    # Virtual upgrade solo toca play_targets, sin torrents
    up = lists.upgrade_cutoff_pointers(db, keys=["movie:tmdb:100"])
    assert up["checked"] == 1

    # Blocklist veta el release
    blk = lists.block_release(db, rel_id, reason="mala calidad")
    assert blk["release_id"] == rel_id
    bl_rows = lists.list_blocklist(db)
    assert len(bl_rows) == 1
    assert bl_rows[0]["reason"] == "mala calidad"

    assert lists.unblock_release(db, rel_id)
    assert lists.list_blocklist(db) == []


def test_import_list_crud_and_run_with_mock(env):
    db, cfg = env
    il = lists.save_import_list(db, {
        "name": "Coleccion Matrix",
        "kind": "tmdb_collection",
        "source_ref": "2344",
        "media_kind": "movie",
    })
    assert il["name"] == "Coleccion Matrix"

    class FakeTmdb:
        def collection_details(self, cid):
            return {
                "id": cid,
                "parts": [
                    {"id": 603, "title": "Matrix", "release_date": "1999-03-30", "popularity": 90},
                    {"id": 604, "title": "Matrix Reloaded", "release_date": "2003-05-15", "popularity": 70},
                ],
            }

    res = lists.run_import_list(db, cfg, il["id"], tmdb_client=FakeTmdb())
    assert res["movies_found"] == 2
    assert res["inserted_movies"] == 2
    assert lists.delete_import_list(db, il["id"])


def test_calendar_range_and_filters(env):
    db, _ = env
    # Solo Pelicula Accion cae en diciembre 2026
    rows = queries.calendar(db, start="2026-11-01", end="2026-12-31", kind="movie", monitored_only=True)
    assert [r["tmdb"] for r in rows] == [100]
    rows_empty = queries.calendar(db, start="2026-11-01", end="2026-12-31", kind="series")
    assert rows_empty == []
