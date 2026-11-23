"""Fallos del motor: qBittorrent reiniciado o caído.

El proxy no debe romper la conexión ni culpar a la fuente cuando el que
falla es qBittorrent. Estos casos fijan ese contrato.
"""

import sqlite3
from pathlib import Path

import pytest

from catalogo import play_intents
from catalogo.db import init_db
from catalogo.play_service import EngineUnavailable, PlayService
from catalogo.qbittorrent import QbittorrentError

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"
TORRENT = b"d4:info4:teste"


class DownQbit:
    """qBittorrent reiniciándose: toda llamada falla."""

    def torrents(self, category=None):
        raise QbittorrentError("qBittorrent no responde")

    def files(self, info_hash):
        raise QbittorrentError("qBittorrent no responde")

    def properties(self, info_hash):
        raise QbittorrentError("qBittorrent no responde")

    def piece_states(self, info_hash):
        raise QbittorrentError("qBittorrent no responde")

    def add_torrent(self, *args, **kwargs):
        raise QbittorrentError("qBittorrent no responde")

    def pause(self, info_hash):
        raise QbittorrentError("qBittorrent no responde")


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,status) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie:tmdb:1", "movie", 1, "Test", 2025, "COLD"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,"
        "privacy,seeders) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2 (API)", "Test.mkv", 1000, "alive",
         "prowlarr", "private", 5),
    )
    conn.commit()
    conn.close()
    play_intents.create(str(db), "movie:tmdb:1")
    return db


def test_ensure_reports_engine_unavailable_when_qbit_is_down(tmp_path):
    db = seed(tmp_path)
    service = PlayService(
        str(db), DownQbit(), DownQbit(), data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    with pytest.raises(EngineUnavailable):
        service.ensure("movie:tmdb:1", torrent_bytes=TORRENT)


def test_wait_stream_ready_returns_false_instead_of_raising(tmp_path):
    db = seed(tmp_path)
    service = PlayService(
        str(db), DownQbit(), DownQbit(), data_path=str(tmp_path),
        save_path=str(tmp_path / "ondemand"), reserve_bytes=0,
    )
    handle = {"info_hash": "abc", "file_index": 0, "release_id": 1}
    assert service.wait_stream_ready(handle, timeout=0.3, interval=0.1) is False
