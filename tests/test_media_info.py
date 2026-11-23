import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.media_info import _normalize, build_media_info

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status,"
        "runtime_seconds) VALUES(?,?,?,?,?,?,?,?)",
        (
            "series:tmdb:1:1",
            "series:tmdb:1:1",
            "series",
            1,
            "Nocturne",
            2026,
            "COLD",
            2880,
        ),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "source,privacy,seeders,quality) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            asset_id,
            "HD-Olimpo (API)",
            "Nocturne S01E01 [APTV WEB-DL 1080p AVC ES DD+ Atmos 5.1]"
            "[HDO].mkv SPANiSH",
            4_000_000_000,
            "alive",
            "prowlarr",
            "private",
            10,
            "1080p",
        ),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.commit()
    conn.close()
    return db, asset_id, release_id


def test_media_info_falls_back_to_the_release_title(tmp_path):
    db, _asset_id, release_id = seed(tmp_path)
    info = build_media_info(
        str(db), "series:tmdb:1:1", release_id=release_id
    )
    assert info is not None
    assert info["source"] == "release"
    assert info["container"] == "matroska,webm"
    assert info["duration_seconds"] == 2880
    assert info["video"]["codec"] == "h264"
    assert info["video"]["width"] == 1920
    assert info["video"]["height"] == 1080
    assert info["audio"][0]["codec"] == "eac3"
    assert info["audio"][0]["channels"] == 6
    assert info["audio"][0]["language"] == "spa"


def test_media_info_normalizes_ffprobe_streams():
    data = {
        "format": {
            "format_name": "matroska,webm",
            "duration": "3224.544",
            "size": "123",
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1918,
                "height": 872,
                "profile": "High",
                "bits_per_raw_sample": "8",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "tags": {"language": "spa"},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "spa"},
            },
        ],
    }
    info = _normalize(data, 4_664_225_386)
    assert info["source"] == "ffprobe"
    assert info["duration_seconds"] == 3224.544
    assert info["size_bytes"] == 4_664_225_386
    assert info["video"]["codec"] == "h264"
    assert info["video"]["bit_depth"] == 8
    assert info["audio"][0]["channels"] == 6
    assert info["audio"][0]["language"] == "spa"
    assert info["subtitles"][0]["codec"] == "subrip"
