from datetime import datetime, timezone
from pathlib import Path

from catalogo.db import init_db
from catalogo.jellyfin_leases import key_from_strm, touch_active_leases
from catalogo.stream_leases import active

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc)


def test_key_from_strm(tmp_path):
    path = tmp_path / "Ep.strm"
    path.write_text(
        "http://127.0.0.1:8773/play?key=series%3Atmdb%3A1%3A2%3A3&download=0\n",
        encoding="utf-8",
    )
    assert key_from_strm(str(path)) == "series:tmdb:1:2:3"
    assert key_from_strm(str(tmp_path / "movie.mkv")) is None
    palantir = tmp_path / "P.strm"
    palantir.write_text(
        "http://127.0.0.1:8766/play?ids=abc\n", encoding="utf-8"
    )
    assert key_from_strm(str(palantir)) is None


def test_touch_active_leases_for_playing_strm(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    path = tmp_path / "Ep.strm"
    path.write_text(
        "http://127.0.0.1:8773/play?key=series%3Atmdb%3A1%3A2%3A3&download=0\n",
        encoding="utf-8",
    )
    sessions = [
        {
            "Id": "abc",
            "Client": "Medusa",
            "NowPlayingItem": {"Name": "Ep", "Path": str(path)},
            "PlayState": {"PositionTicks": 1000},
        },
        {
            "Id": "idle",
            "Client": "Web",
            "NowPlayingItem": None,
        },
    ]
    report = touch_active_leases(
        str(db), api_key="x", sessions=sessions, now=NOW
    )
    assert report["sessions"] == 2
    assert report["touched"][0]["key"] == "series:tmdb:1:2:3"
    assert active(
        str(db), "series:tmdb:1:2:3", grace_seconds=300, now=NOW
    )
