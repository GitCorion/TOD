from pathlib import Path

from catalogo import jobs
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_publish_jobs_can_queue_together(tmp_path):
    db = tmp_path / "tod.db"
    init_db(db, MIGRATIONS)
    first = jobs.create_job(db, "publish-now", allow_duplicate=True)
    second = jobs.create_job(db, "publish-now", allow_duplicate=True)
    assert second > first


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
