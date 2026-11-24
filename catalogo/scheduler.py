"""Scheduler interno de TOD.

Sustituye a los timers systemd cuando la aplicación corre como servicio único.
Cada tarea se persiste en la tabla `settings` y se ejecuta a través de
`jobs.JobRunner`, que ya serializa los trabajos y los registra en `jobs`.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from . import jobs, settings

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MIN_INTERVAL = 10
MAX_INTERVAL = 7 * 24 * 3600


@dataclass(frozen=True)
class TaskSpec:
    name: str
    label: str
    kind: str
    description: str
    schedule: str = "interval"  # interval | daily | weekly
    interval: int = 300
    at: str = "04:20"
    weekday: int = 0  # lunes=0, domingo=6
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


DEFAULT_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "worker-cycle", "Ciclo del worker", "worker-cycle",
        "Sincroniza qBittorrent, repara, reseed y limpia.",
        interval=300,
    ),
    TaskSpec(
        "move-completed", "Mover completados a archivo", "move-completed",
        "Mueve los torrents on-demand terminados al archivo sin dejar de sembrar.",
        interval=600,
        params={"execute": True, "limit": 10, "grace": 300},
    ),
    TaskSpec(
        "attention-check", "Comprobación de estado", "attention-check",
        "Prueba las integraciones y actualiza los avisos de Inicio.",
        interval=300,
    ),
    TaskSpec(
        "arr-mirror", "Espejo Radarr/Sonarr", "arr-mirror",
        "Importa las altas de Radarr/Sonarr, busca fuentes y crea sus entradas de Jellyfin.",
        interval=1800,
        params={"source_limit": 50, "publish_limit": 200, "delay": 0.2},
    ),
    TaskSpec(
        "qbit-limits", "Límites de qBittorrent", "qbit-limits",
        "Aplica los límites por categoría de Radarr, Sonarr y on-demand.",
        interval=60,
    ),
    TaskSpec(
        "rss-sync", "Novedades RSS", "rss-sync",
        "Captura novedades de Prowlarr/Jackett y crea sus entradas de Jellyfin.",
        interval=1800,
        params={"limit": 200, "delay": 0.3},
    ),
    TaskSpec(
        "bridge", "Puente a Radarr/Sonarr", "bridge",
        "Copia a los *arr lo descargado por TOD.",
        interval=1800,
        params={"limit": 1, "execute": True},
    ),
    TaskSpec(
        "monitor", "Seguimiento diario", "monitor",
        "Busca fuentes de los títulos en seguimiento que ya están disponibles.",
        schedule="daily", at="04:20",
        params={"limit": 100, "delay": 2},
    ),
    TaskSpec(
        "restore-strm", "Reconciliar .strm", "restore-strm",
        "Repone los .strm que falten y poda los de títulos sin fuente.",
        schedule="daily", at="04:30",
        params={"prune": True, "min_seeders": 1},
    ),
    TaskSpec(
        "backfill", "Fondo antiguo", "backfill",
        "Lote semanal de títulos antiguos pendientes.",
        schedule="weekly", weekday=6, at="03:00",
        params={"limit": 250, "delay": 8},
    ),
    TaskSpec(
        "metadata", "Metadatos TMDB", "metadata",
        "Rellena póster, sinopsis y nota de títulos sin metadatos.",
        schedule="weekly", weekday=0, at="05:00",
        params={"limit": 0, "delay": 0.08, "workers": 6},
        enabled=False,
    ),
)

BY_NAME = {task.name: task for task in DEFAULT_TASKS}


def _key(name: str) -> str:
    return f"scheduler.{name}"


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def load_task(db_path: str | Path, spec: TaskSpec) -> dict[str, Any]:
    state: dict[str, Any] = {
        "enabled": spec.enabled,
        "interval": spec.interval,
        "at": spec.at,
        "weekday": spec.weekday,
        "params": dict(spec.params),
    }
    raw = settings.get_setting(db_path, _key(spec.name))
    if not raw:
        return state
    try:
        saved = json.loads(raw)
    except (TypeError, ValueError):
        return state
    if not isinstance(saved, dict):
        return state
    if isinstance(saved.get("enabled"), bool):
        state["enabled"] = saved["enabled"]
    if isinstance(saved.get("interval"), int):
        state["interval"] = max(MIN_INTERVAL, min(MAX_INTERVAL, saved["interval"]))
    if isinstance(saved.get("at"), str) and TIME_RE.match(saved["at"]):
        state["at"] = saved["at"]
    if isinstance(saved.get("weekday"), int) and 0 <= saved["weekday"] <= 6:
        state["weekday"] = saved["weekday"]
    if isinstance(saved.get("params"), dict):
        state["params"] = dict(saved["params"])
    return state


def save_task(
    db_path: str | Path, name: str, patch: dict[str, Any]
) -> dict[str, Any]:
    spec = BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"tarea desconocida: {name}")
    if not isinstance(patch, dict):
        raise ValueError("la tarea debe ser un objeto")
    state = load_task(db_path, spec)
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise ValueError("enabled debe ser booleano")
        state["enabled"] = patch["enabled"]
    if "interval" in patch:
        try:
            value = int(patch["interval"])
        except (TypeError, ValueError) as exc:
            raise ValueError("interval debe ser un entero") from exc
        if value < MIN_INTERVAL or value > MAX_INTERVAL:
            raise ValueError(
                f"interval debe estar entre {MIN_INTERVAL} y {MAX_INTERVAL}"
            )
        state["interval"] = value
    if "at" in patch:
        value = str(patch["at"] or "").strip()
        if not TIME_RE.match(value):
            raise ValueError("at debe tener formato HH:MM")
        state["at"] = value
    if "weekday" in patch:
        try:
            value = int(patch["weekday"])
        except (TypeError, ValueError) as exc:
            raise ValueError("weekday debe ser un entero") from exc
        if value < 0 or value > 6:
            raise ValueError("weekday debe estar entre 0 y 6")
        state["weekday"] = value
    if "params" in patch:
        if not isinstance(patch["params"], dict):
            raise ValueError("params debe ser un objeto")
        state["params"] = dict(patch["params"])
    settings.set_setting(
        db_path, _key(name), json.dumps(state, ensure_ascii=False)
    )
    return state


def _last_job(db_path: str | Path, kind: str) -> dict[str, Any] | None:
    recent = jobs.list_jobs(db_path, limit=200)
    return next((job for job in recent if job.get("kind") == kind), None)


def _last_dt(job: dict[str, Any] | None) -> datetime | None:
    if not job:
        return None
    return _parse_dt(job.get("finished_at") or job.get("created_at"))


def _at_time(day, state: dict[str, Any], tzinfo) -> datetime:
    hour, minute = (int(part) for part in state["at"].split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=tzinfo)


def compute_next(
    spec: TaskSpec,
    state: dict[str, Any],
    last: datetime | None,
    now: datetime,
) -> datetime | None:
    if not state.get("enabled"):
        return None
    if spec.schedule == "interval":
        base = last or now
        return base + timedelta(seconds=int(state.get("interval") or 300))
    if spec.schedule == "daily":
        candidate = _at_time(now.date(), state, now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=1)
        if last is not None and last >= candidate:
            candidate += timedelta(days=1)
        return candidate
    if spec.schedule == "weekly":
        target = int(state.get("weekday") or 0)
        days = (target - now.weekday()) % 7
        candidate = _at_time(now.date() + timedelta(days=days), state, now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=7)
        if last is not None and last >= candidate:
            candidate += timedelta(days=7)
        return candidate
    return None


def _brief_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    result = job.get("result") or {}
    keys = ("ok", "written", "skipped", "errors", "exported_keys")
    return {
        "id": job.get("id"),
        "state": job.get("state"),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "message": job.get("message"),
        "error": job.get("error"),
        "result": {
            key: result[key] for key in keys if isinstance(result, dict) and key in result
        },
    }


def list_tasks(
    db_path: str | Path, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    current = now or _now()
    items: list[dict[str, Any]] = []
    for spec in DEFAULT_TASKS:
        state = load_task(db_path, spec)
        job = _last_job(db_path, spec.kind)
        last = _last_dt(job)
        nxt = compute_next(spec, state, last, current)
        items.append(
            {
                "name": spec.name,
                "label": spec.label,
                "kind": spec.kind,
                "description": spec.description,
                "schedule": spec.schedule,
                **state,
                "next_run": nxt.isoformat() if nxt else None,
                "last": _brief_job(job),
            }
        )
    return items


class Scheduler:
    def __init__(
        self,
        db_path: str | Path,
        runner: jobs.JobRunner,
        *,
        tick_seconds: float = 5.0,
        clock=_now,
    ):
        self.db_path = Path(db_path)
        self.runner = runner
        self.tick_seconds = max(1.0, float(tick_seconds))
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cooldown: dict[str, float] = {}
        self._initial_due: dict[str, datetime] = {}
        self._initialized = False

    def _prepare_initial(self, now: datetime) -> None:
        if self._initialized:
            return
        for index, spec in enumerate(DEFAULT_TASKS):
            if spec.schedule == "interval":
                self._initial_due[spec.name] = now + timedelta(
                    seconds=10 + index * 5
                )
        self._initialized = True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="tod-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # El scheduler nunca debe tumbar la web.
                pass
            self._stop.wait(self.tick_seconds)

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or self.clock()
        self._prepare_initial(current)
        submitted: list[dict[str, Any]] = []
        for spec in DEFAULT_TASKS:
            state = load_task(self.db_path, spec)
            if not state.get("enabled"):
                continue
            job = _last_job(self.db_path, spec.kind)
            last = _last_dt(job)
            if last is None and spec.name in self._initial_due:
                nxt: datetime | None = self._initial_due[spec.name]
            else:
                nxt = compute_next(spec, state, last, current)
            if nxt is None or current < nxt:
                continue
            if self._cooldown.get(spec.name, 0) > current.timestamp():
                continue
            try:
                job_id = self.runner.submit(
                    spec.kind,
                    state.get("params") or {},
                    requested_by="scheduler",
                )
            except jobs.JobConflict:
                self._cooldown[spec.name] = current.timestamp() + 60
                continue
            except Exception:
                self._cooldown[spec.name] = current.timestamp() + 60
                continue
            self._initial_due.pop(spec.name, None)
            submitted.append({"name": spec.name, "job_id": job_id})
        return submitted

    def run_now(self, name: str) -> dict[str, Any]:
        spec = BY_NAME.get(name)
        if spec is None:
            raise ValueError(f"tarea desconocida: {name}")
        state = load_task(self.db_path, spec)
        job_id = self.runner.submit(
            spec.kind,
            state.get("params") or {},
            requested_by="scheduler-manual",
        )
        return {"name": name, "job_id": job_id}
