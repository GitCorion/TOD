from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catalogo import admin, scheduler
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def _db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def test_defaults_are_exposed(tmp_path):
    db = _db(tmp_path)
    tasks = scheduler.list_tasks(
        db, now=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    )
    names = [task["name"] for task in tasks]
    assert "worker-cycle" in names
    assert "move-completed" in names
    assert "rss-sync" in names
    assert "restore-strm" in names
    move = next(task for task in tasks if task["name"] == "move-completed")
    assert move["kind"] == "move-completed"
    assert move["enabled"] is True
    assert move["interval"] == 600
    assert move["params"] == {"execute": True, "limit": 10, "grace": 300}
    assert "move-completed" in admin.JOB_HANDLERS
    worker = next(task for task in tasks if task["name"] == "worker-cycle")
    assert worker["enabled"] is True
    assert worker["next_run"] is not None
    metadata = next(task for task in tasks if task["name"] == "metadata")
    assert metadata["enabled"] is False


def test_save_task_persists_and_validates(tmp_path):
    db = _db(tmp_path)
    state = scheduler.save_task(
        db, "rss-sync", {"enabled": False, "interval": 900}
    )
    assert state["enabled"] is False
    assert state["interval"] == 900
    loaded = scheduler.load_task(db, scheduler.BY_NAME["rss-sync"])
    assert loaded["enabled"] is False
    assert loaded["interval"] == 900

    with pytest.raises(ValueError):
        scheduler.save_task(db, "rss-sync", {"interval": 1})
    with pytest.raises(ValueError):
        scheduler.save_task(db, "monitor", {"at": "99:99"})
    with pytest.raises(ValueError):
        scheduler.save_task(db, "backfill", {"weekday": 9})
    with pytest.raises(ValueError):
        scheduler.save_task(db, "inventada", {})


def test_next_daily_and_weekly():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)  # viernes
    daily = scheduler.BY_NAME["monitor"]
    state = {
        "enabled": True, "at": "04:20", "interval": 300, "weekday": 4,
        "params": {},
    }
    nxt = scheduler.compute_next(daily, state, None, now)
    assert nxt == datetime(2026, 11, 21, 4, 20, tzinfo=timezone.utc)

    weekly = scheduler.BY_NAME["backfill"]
    state = {**state, "at": "03:00", "weekday": 6}
    nxt = scheduler.compute_next(weekly, state, None, now)
    assert nxt == datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)

    disabled = {**state, "enabled": False}
    assert scheduler.compute_next(weekly, disabled, None, now) is None


def test_scheduler_tick_submits_due_tasks(tmp_path):
    db = _db(tmp_path)
    for task in scheduler.DEFAULT_TASKS:
        if task.name != "worker-cycle":
            scheduler.save_task(db, task.name, {"enabled": False})

    class FakeRunner:
        def __init__(self):
            self.calls = []

        def submit(self, kind, params=None, *, requested_by=None):
            self.calls.append((kind, params, requested_by))
            return len(self.calls)

    runner = FakeRunner()
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    sched = scheduler.Scheduler(db, runner, clock=lambda: now)
    sched._prepare_initial(now)
    assert sched.tick(now + timedelta(seconds=5)) == []
    submitted = sched.tick(now + timedelta(seconds=11))
    assert submitted == [{"name": "worker-cycle", "job_id": 1}]
    assert runner.calls[0][0] == "worker-cycle"
    assert runner.calls[0][2] == "scheduler"


def test_scheduler_submits_move_task_with_real_execution(tmp_path):
    db = _db(tmp_path)
    for task in scheduler.DEFAULT_TASKS:
        if task.name != "move-completed":
            scheduler.save_task(db, task.name, {"enabled": False})

    class FakeRunner:
        def __init__(self):
            self.calls = []

        def submit(self, kind, params=None, *, requested_by=None):
            self.calls.append((kind, params, requested_by))
            return len(self.calls)

    runner = FakeRunner()
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    sched = scheduler.Scheduler(db, runner, clock=lambda: now)
    sched._prepare_initial(now)
    assert sched.tick(now + timedelta(seconds=11)) == []
    submitted = sched.tick(now + timedelta(seconds=61))
    assert submitted == [{"name": "move-completed", "job_id": 1}]
    assert runner.calls[0][0] == "move-completed"
    assert runner.calls[0][1]["execute"] is True


def test_scheduler_cooldown_on_conflict(tmp_path):
    db = _db(tmp_path)
    for task in scheduler.DEFAULT_TASKS:
        scheduler.save_task(db, task.name, {"enabled": False})
    scheduler.save_task(db, "worker-cycle", {"enabled": True})

    class BusyRunner:
        def submit(self, *args, **kwargs):
            from catalogo.jobs import JobConflict

            raise JobConflict("ocupado")

    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    sched = scheduler.Scheduler(db, BusyRunner(), clock=lambda: now)
    sched._prepare_initial(now)
    assert sched.tick(now + timedelta(seconds=11)) == []
    assert sched._cooldown["worker-cycle"] > now.timestamp()


def test_run_now_unknown_task(tmp_path):
    db = _db(tmp_path)
    sched = scheduler.Scheduler(db, None)
    with pytest.raises(ValueError):
        sched.run_now("inventada")


def test_scheduler_triggers_daily_and_weekly_tasks(tmp_path):
    db = _db(tmp_path)
    for task in scheduler.DEFAULT_TASKS:
        scheduler.save_task(db, task.name, {"enabled": False})
    scheduler.save_task(db, "restore-strm", {"enabled": True, "at": "04:30"})
    scheduler.save_task(
        db, "backfill", {"enabled": True, "weekday": 6, "at": "03:00"}
    )

    class RecordingRunner:
        def __init__(self, db_path):
            self.db_path = db_path
            self.calls = []

        def submit(self, kind, params=None, *, requested_by=None):
            from catalogo.db import connect

            self.calls.append((kind, params, requested_by))
            with connect(self.db_path) as conn:
                cur = conn.execute(
                    "INSERT INTO jobs(kind, params, state, created_at, finished_at) "
                    "VALUES(?, '{}', 'ok', ?, ?)",
                    (
                        kind,
                        self._now.isoformat(),
                        self._now.isoformat(),
                    ),
                )
                conn.commit()
                return cur.lastrowid

    runner = RecordingRunner(db)
    # Domingo 2026-11-22 a las 04:35 UTC: ambos slots (03:00 semanal y 04:30 diario) ya vencieron.
    runner._now = datetime(2026, 11, 22, 4, 35, tzinfo=timezone.utc)
    sched = scheduler.Scheduler(db, runner, clock=lambda: runner._now)
    submitted = sched.tick(runner._now)
    names = {item["name"] for item in submitted}
    assert names == {"restore-strm", "backfill"}

    # Cinco minutos después no vuelven a dispararse en el mismo día/semana.
    runner._now = datetime(2026, 11, 22, 4, 40, tzinfo=timezone.utc)
    assert sched.tick(runner._now) == []

    # Al día siguiente a las 04:31 solo vuelve a saltar restore-strm (diaria).
    runner._now = datetime(2026, 11, 23, 4, 31, tzinfo=timezone.utc)
    submitted_next = sched.tick(runner._now)
    assert [item["name"] for item in submitted_next] == ["restore-strm"]
