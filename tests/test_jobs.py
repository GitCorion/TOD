from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from catalogo import admin, jobs
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def test_publish_jobs_can_queue_together(tmp_path):
    db = tmp_path / "tod.db"
    init_db(db, MIGRATIONS)
    first = jobs.create_job(db, "publish-now", allow_duplicate=True)
    second = jobs.create_job(db, "publish-now", allow_duplicate=True)
    assert second > first


def test_move_handler_uses_configured_destination(tmp_path, monkeypatch):
    captured = {}

    class FakeClient:
        pass

    monkeypatch.setattr(admin, "_qb_write", lambda config: FakeClient())

    def fake_move(db_path, client, **kwargs):
        captured.update(kwargs)
        return {"moved": [], "errors": []}

    monkeypatch.setattr(admin, "move_completed", fake_move)
    config = Config(
        db_path=tmp_path / "tod.db",
        legacy_db=tmp_path / "assets.db",
        library_root=Path("/mnt/fichier"),
        seed_root=tmp_path / "semillas",
        migrations_dir=tmp_path / "migrations",
        prowlarr_url="http://127.0.0.1:9696/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1340",
        qbittorrent_user="qbittorrent",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:9117/api/v2.0",
        jackett_api_key=None,
        max_stream_bytes=0,
        move_target=Path("/mnt/otro/archivo"),
    )
    ctx = SimpleNamespace(
        config=config,
        params={"execute": True, "limit": 3},
        report=lambda *args, **kwargs: None,
    )
    handler = admin.JOB_HANDLERS["move-completed"]
    assert handler(ctx) == {"moved": [], "errors": []}
    assert captured["target"] == "/mnt/otro/archivo"
    assert captured["dry_run"] is False
    assert captured["limit"] == 3

    ctx.config = replace(config, move_target=None)
    handler(ctx)
    assert captured["target"] == "/mnt/fichier/descargas"


def test_internal_jobs_can_be_hidden(tmp_path):
    db = tmp_path / "tod.db"
    init_db(db, MIGRATIONS)
    jobs.create_job(db, "attention-check")
    jobs.create_job(db, "publish-now", {"keys": ["movie:tmdb:1"]})

    visible = jobs.list_jobs(db, exclude_kinds=("attention-check",))
    assert [job["kind"] for job in visible] == ["publish-now"]

    everything = jobs.list_jobs(db)
    assert {job["kind"] for job in everything} == {
        "attention-check",
        "publish-now",
    }
