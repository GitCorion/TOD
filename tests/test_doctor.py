import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.doctor import run_doctor

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeQbit:
    def app_version(self):
        return "test"

    def torrents(self):
        return [
            {"state": "checkingUP", "category": "radarr"}
            for _ in range(60)
        ]


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title) VALUES(?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test"),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.mkv", 1000, "alive"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO seed_commitments(asset_id,release_id,tracker,size_bytes,"
        "state) VALUES(?,?,?,?,?)",
        (asset_id, release_id, "TrackerEspanol2", 1000, "active"),
    )
    conn.commit()
    conn.close()
    return db


def test_doctor_detects_orphans_and_qbit_checks(tmp_path):
    db = seed(tmp_path)
    report = run_doctor(
        str(db), qbit=FakeQbit(), paths=(str(tmp_path),)
    )
    assert report["mounts"][str(tmp_path)]["writable"] is True
    assert report["db"]["orphan_commitments"] == 1
    assert report["qbit"]["checking"] == 60
    assert any("verificando" in item for item in report["warnings"])
    assert any("sin torrent" in item for item in report["warnings"])


class UnsafePrivateQbit(FakeQbit):
    def torrents(self):
        return []

    def preferences(self):
        return {
            "dht": True,
            "pex": False,
            "lsd": False,
            "anonymous_mode": True,
            "listen_port": 6881,
            "incomplete_files_ext": True,
        }


def test_doctor_warns_on_unsafe_private_qbit_settings(tmp_path):
    db = seed(tmp_path)
    report = run_doctor(str(db), qbit=UnsafePrivateQbit(), paths=(str(tmp_path),))
    joined = " | ".join(report["warnings"])
    assert "DHT/PeX/LSD" in joined
    assert "anonymous_mode" in joined
    assert "6881" in joined
    assert ".!qB" in joined
