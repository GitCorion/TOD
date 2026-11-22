from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalogo import play_intents
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def test_create_active_consume_and_cleanup(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)

    intent = play_intents.create(
        str(db), "movie:tmdb:1", ttl_minutes=5, now=NOW,
        client_ip="127.0.0.1", user_agent="test",
    )
    assert intent["media_id"] == "movie:tmdb:1"
    active = play_intents.active(str(db), "movie:tmdb:1", now=NOW)
    assert active["id"] == intent["id"]

    # Reutiliza el activo en lugar de crear otro.
    again = play_intents.create(
        str(db), "movie:tmdb:1", ttl_minutes=5, now=NOW
    )
    assert again["id"] == intent["id"]

    assert play_intents.consume(str(db), intent["id"], now=NOW)
    assert play_intents.active(str(db), "movie:tmdb:1", now=NOW) is None

    # Expirado.
    old = play_intents.create(
        str(db), "movie:tmdb:2", ttl_minutes=1, now=NOW
    )
    later = NOW + timedelta(minutes=2)
    assert play_intents.active(str(db), "movie:tmdb:2", now=later) is None
    assert play_intents.cleanup(str(db), now=later) >= 1
    assert old["id"]
