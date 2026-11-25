import sqlite3
from pathlib import Path

from catalogo import profiles
from catalogo.db import init_db
from catalogo.release_evaluator import evaluate_release
from catalogo.resolver import Resolver

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed_db(tmp_path):
    db = tmp_path / "tod.db"
    init_db(db, MIGRATIONS)
    profiles.ensure_defaults(db)
    return db


def test_default_profile_prefers_1080p_with_4k_fallback_and_blocks_cam_brdisk(tmp_path):
    db = seed_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,original_language,original_country,runtime_seconds) "
            "VALUES('movie:tmdb:100','movie',100,'Film',2025,'en','US',6000)"
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT INTO releases(asset_id,tracker,title,quality,source_type,codec,hdr,audio,size_bytes,seeders,health) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (aid, "HDO", "Film (2025) CAM ES", "unknown", "cam", "H264", None, "castellano", 1_500_000_000, 50, "alive"),
                (aid, "HDO", "Film (2025) BRDISK 1080p AVC ES", "1080p", "brdisk", "H264", None, "castellano", 45_000_000_000, 30, "alive"),
                (aid, "HDO", "Film (2025) 2160p WEB-DL HEVC DV ES", "4K", "webdl", "HEVC", "DV", "castellano", 18_000_000_000, 20, "alive"),
                (aid, "HDO", "Film (2025) 1080p BluRay Remux ES", "1080p", "remux", "H264", None, "castellano", 28_000_000_000, 15, "alive"),
                (aid, "HDO", "Film (2025) 1080p NF WEB-DL HEVC ES", "1080p", "webdl", "HEVC", None, "castellano", 4_500_000_000, 10, "alive"),
            ],
        )
        conn.commit()

    sources = Resolver(db).sources("movie:tmdb:100")
    titles = [s["title"] for s in sources]
    assert "CAM" not in " ".join(titles)
    assert "BRDISK" not in " ".join(titles)
    assert titles[0] == "Film (2025) 1080p NF WEB-DL HEVC ES"
    assert titles[1] == "Film (2025) 1080p BluRay Remux ES"
    assert titles[2] == "Film (2025) 2160p WEB-DL HEVC DV ES"

    # Interactive search shows rejection reasons for CAM & BRDISK
    all_sources = Resolver(db).sources("movie:tmdb:100", include_rejected=True)
    rejected = [s for s in all_sources if not s["allowed"]]
    assert len(rejected) == 2
    assert any("CAM" in r for s in rejected for r in s["rejections"])
    assert any("BRDISK" in r for s in rejected for r in s["rejections"])

    # If all 1080p die, 4K acts seamlessly as fallback
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE releases SET health='dead' WHERE quality='1080p'")
        conn.commit()
    res = Resolver(db).resolve("movie:tmdb:100")
    assert res.source == "torrent"
    assert res.size == 18_000_000_000


def test_4k_hdr_profile_prefers_4k_dv_over_1080p(tmp_path):
    db = seed_db(tmp_path)
    p4k = next(p for p in profiles.list_profiles(db) if "4K HDR" in p["name"])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,quality_profile_id) "
            "VALUES('movie:tmdb:200','movie',200,'Dune',2024,?)",
            (p4k["id"],),
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT INTO releases(asset_id,tracker,title,quality,source_type,codec,hdr,audio,size_bytes,seeders,health) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (aid, "HDO", "Dune (2024) 1080p WEB-DL ES", "1080p", "webdl", "H264", None, "castellano", 4_000_000_000, 30, "alive"),
                (aid, "HDO", "Dune (2024) 2160p WEB-DL HEVC DV-HDR10 ES", "4K", "webdl", "HEVC", "DV-HDR10", "castellano", 22_000_000_000, 12, "alive"),
            ],
        )
        conn.commit()
    sources = Resolver(db).sources("movie:tmdb:200")
    assert sources[0]["quality"] == "4K"
    assert sources[0]["cf_score"] >= 150


def test_streaming_ligero_enforces_mb_per_min_and_cutoff(tmp_path):
    db = seed_db(tmp_path)
    lite = next(p for p in profiles.list_profiles(db) if "Streaming Ligero" in p["name"])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,runtime_seconds,quality_profile_id) "
            "VALUES('movie:tmdb:300','movie',300,'Heavy',2025,6000,?)",
            (lite["id"],),
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT INTO releases(asset_id,tracker,title,quality,source_type,codec,audio,size_bytes,seeders,health) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (aid, "HDO", "Heavy 720p ES", "720p", "webdl", "H264", "castellano", 2_000_000_000, 10, "alive"),
                (aid, "HDO", "Heavy 1080p Remux ES", "1080p", "remux", "H264", "castellano", 25_000_000_000, 15, "alive"),
            ],
        )
        conn.commit()

    # Only 720p is eligible -> cutoff (1080p) is unmet!
    status = Resolver(db).cutoff_status("movie:tmdb:300")
    assert status["cutoff_met"] is False
    assert status["cutoff_quality"] == "1080p"

    # When a lightweight 1080p HEVC arrives, cutoff becomes met without touching qBittorrent
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,quality,source_type,codec,audio,size_bytes,seeders,health) "
            "VALUES((SELECT id FROM media_items WHERE key='movie:tmdb:300'),'HDO','Heavy 1080p WEB-DL x265 ES','1080p','webdl','HEVC','castellano',3500000000,8,'alive')"
        )
        conn.commit()
    status2 = Resolver(db).cutoff_status("movie:tmdb:300")
    assert status2["cutoff_met"] is True
    assert Resolver(db).sources("movie:tmdb:300")[0]["quality"] == "1080p"
