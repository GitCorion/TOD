import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.release_parse import episode_in_release, parse_release
from catalogo.release_parser import backfill_releases, parse_release_info

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def test_parse_real_spanish_tracker_titles():
    remux = parse_release_info(
        "Transformers One (2024) [Audio Editado] "
        "[UHD Remux 2160p HEVC DV-HDR10 ES True-HD Atmos 7.1 Subs]HDO.mkv SPANiSH"
    )
    assert remux.quality == "4K"
    assert remux.source_type == "remux"
    assert remux.codec == "HEVC"
    assert remux.hdr == "DV-HDR10"
    assert remux.audio == "castellano"
    assert remux.audio_codec == "TrueHD Atmos"
    assert remux.audio_channels == "7.1"
    assert remux.edition == "Audio Editado"

    brdisk = parse_release_info(
        "Desafio total (2012) (E Especial 2 Discos) "
        "[BRDISK 1080p AVC MULTi SPANiSH ENGLiSH TrueHD 5.1 Subs][HDO]"
    )
    assert brdisk.source_type == "brdisk"
    assert brdisk.flags["disc_image"] is True
    assert brdisk.flags["multi_audio"] is True
    assert brdisk.codec == "H264"
    assert brdisk.release_group == "HDO"
    assert brdisk.edition == "Edición Especial"

    lat_false_spanish = parse_release_info(
        "Dear Killer Nannies (2026) S01 [PACK]"
        "[DSNP WEB-DL 1080p AVC LAT DD+ Atmos 5.1][HDO] SPANiSH"
    )
    assert lat_false_spanish.audio == "latino"
    assert lat_false_spanish.streaming_service == "DSNP"
    assert lat_false_spanish.source_type == "webdl"
    assert lat_false_spanish.audio_codec == "DD+ Atmos"

    dual_both = parse_release_info(
        "Finding Her Edge S01 1080p BluRay Dual-Audio DD+ 5.1 x264 Latino Castellano"
    )
    assert dual_both.audio == "castellano"
    assert dual_both.flags["multi_audio"] is True

    vose_subs = parse_release_info("Pelicula Genial 2025 1080p Subs Esp")
    assert vose_subs.audio == "vose"

    scene_4k = parse_release_info(
        "Maximum.Pleasure.Guaranteed.S01E06.Rosebuds.2160p.ATVP.WEB-DL.DDP5.1.Atmos.H.265-RAWR.mkv"
    )
    assert scene_4k.quality == "4K"
    assert scene_4k.source_type == "webdl"
    assert scene_4k.streaming_service == "ATVP"
    assert scene_4k.codec == "HEVC"
    assert scene_4k.audio_codec == "DD+ Atmos"
    assert scene_4k.audio_channels == "5.1"
    assert scene_4k.release_group == "RAWR"


def test_parse_anime_and_daily_series():
    daily = parse_release(
        "La.Revuelta.2026.03.15.1080p.HDTV.x264", series_type="daily"
    )
    assert daily.air_date == "2026-03-15"
    assert episode_in_release(
        "La.Revuelta.2026.03.15.1080p.HDTV.x264",
        2, 10, series_type="daily", air_date="2026-03-15",
    )

    anime = parse_release(
        "[SubsPlease] One Piece - 1120 (1080p) [ABC123].mkv",
        series_type="anime",
    )
    assert anime.absolute_episode == 1120
    assert episode_in_release(
        "[SubsPlease] One Piece - 1120 (1080p) [ABC123].mkv",
        21, 200, series_type="anime", absolute_episode=1120,
    )


def test_backfill_releases_updates_rich_fields(tmp_path):
    db = tmp_path / "tod.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,original_language,original_country) "
        "VALUES('movie:tmdb:1','movie',1,'Test',2025,'en','US')"
    )
    aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,quality,audio,size_bytes,health) "
        "VALUES(?,'HDO','Test (2025) [NF WEB-DL 1080p HEVC LAT DD+ 5.1][HDO] SPANiSH','1080p','castellano',2000000000,'alive')",
        (aid,),
    )
    conn.commit()
    conn.close()

    report = backfill_releases(str(db), reparse_audio=True)
    assert report["releases_updated"] == 1
    assert report["reclassified_latino"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM releases").fetchone()
    assert row["source_type"] == "webdl"
    assert row["codec"] == "HEVC"
    assert row["audio"] == "latino"
    assert row["audio_codec"] == "EAC3"
    assert row["audio_channels"] == "5.1"
    assert row["streaming_service"] == "NF"
    assert row["release_group"] == "HDO"
    conn.close()
