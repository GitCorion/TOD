"""Cola persistente de tareas de administración.

Las tareas se guardan en la tabla `jobs` para poder supervisarlas y para que
un reinicio no deje estados mentirosos. Solo se ejecuta un trabajo a la vez:
Prowlarr, qBittorrent y archivo no están para recibir ráfagas.
"""

from __future__ import annotations

import json
import queue
import threading
import traceback
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import connect

ACTIVE_STATES = ("queued", "running")
TERMINAL_STATES = ("ok", "error", "cancelled")
MAX_MESSAGE = 500


class JobError(RuntimeError):
    pass


class JobConflict(JobError):
    pass


class JobCancelled(JobError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _row(row) -> dict[str, Any]:
    item = dict(row)
    item["params"] = _loads(item.get("params"))
    item["result"] = _loads(item.get("result"))
    return item


def create_job(
    db_path: str | Path,
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    requested_by: str | None = None,
    allow_duplicate: bool = False,
) -> int:
    with closing(connect(db_path)) as conn:
        if not allow_duplicate:
            current = conn.execute(
                """
                SELECT id FROM jobs
                WHERE kind = ? AND state IN ('queued', 'running')
                ORDER BY id DESC LIMIT 1
                """,
                (kind,),
            ).fetchone()
            if current is not None:
                raise JobConflict(
                    f"ya hay una tarea '{kind}' en curso (id {current['id']})"
                )
        cursor = conn.execute(
            """
            INSERT INTO jobs(kind, params, requested_by)
            VALUES (?, ?, ?)
            """,
            (kind, _json(params or {}), requested_by),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_job(db_path: str | Path, job_id: int) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (int(job_id),)
        ).fetchone()
    return _row(row) if row is not None else None


def list_jobs(
    db_path: str | Path,
    *,
    limit: int = 50,
    state: str | None = None,
    exclude_kinds: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if exclude_kinds:
        clauses.append(
            "kind NOT IN (" + ",".join("?" for _ in exclude_kinds) + ")"
        )
        params.extend(exclude_kinds)
    query = "SELECT * FROM jobs"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(row) for row in rows]


def update_job(
    db_path: str | Path,
    job_id: int,
    *,
    state: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    result: Any = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    fields: list[str] = []
    params: list[Any] = []
    if state is not None:
        fields.append("state = ?")
        params.append(state)
    if progress is not None:
        fields.append("progress = ?")
        params.append(max(0.0, min(float(progress), 1.0)))
    if message is not None:
        fields.append("message = ?")
        params.append(str(message)[:MAX_MESSAGE])
    if result is not None:
        fields.append("result = ?")
        params.append(_json(result))
    if error is not None:
        fields.append("error = ?")
        params.append(str(error)[:4000])
    if started:
        fields.append("started_at = ?")
        params.append(_now())
    if finished:
        fields.append("finished_at = ?")
        params.append(_now())
    if not fields:
        return
    params.append(int(job_id))
    with closing(connect(db_path)) as conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", params
        )
        conn.commit()


def cancel_job(db_path: str | Path, job_id: int) -> dict[str, Any] | None:
    job = get_job(db_path, job_id)
    if job is None:
        return None
    if job["state"] == "queued":
        update_job(
            db_path,
            job_id,
            state="cancelled",
            message="cancelada antes de empezar",
            finished=True,
        )
        return get_job(db_path, job_id)
    if job["state"] == "running":
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET cancel_requested = 1 WHERE id = ?",
                (int(job_id),),
            )
            conn.commit()
        return get_job(db_path, job_id)
    return job


def recover_jobs(db_path: str | Path) -> int:
    """Cancela las tareas que quedaron a medias por un reinicio.

    No son fallos del producto: un reinicio controlado no debe llenar el
    panel de avisos ni contar como error en las métricas.
    """
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET state = 'cancelled',
                error = 'interrumpida por reinicio del servidor',
                message = 'interrumpida por reinicio del servidor',
                finished_at = ?
            WHERE state IN ('queued', 'running')
            """,
            (_now(),),
        )
        conn.commit()
        return int(cursor.rowcount)


@dataclass
class JobContext:
    job_id: int
    kind: str
    params: dict[str, Any]
    db_path: Path
    config: Any
    cancel_event: threading.Event
    _last_progress: float = field(default=0.0)

    def report(self, message: str | None = None, progress: float | None = None) -> None:
        if progress is not None:
            self._last_progress = max(0.0, min(float(progress), 1.0))
        update_job(
            self.db_path,
            self.job_id,
            message=message,
            progress=progress,
        )

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check(self) -> None:
        if self.cancelled():
            raise JobCancelled("tarea cancelada")

    def result(self, value: dict[str, Any]) -> dict[str, Any]:
        return value


JobHandler = Callable[[JobContext], dict[str, Any]]


class JobRunner:
    """Un único hilo trabajador; la cola vive en SQLite."""

    def __init__(
        self,
        db_path: str | Path,
        config: Any,
        handlers: dict[str, JobHandler],
    ):
        self.db_path = Path(db_path)
        self.config = config
        self.handlers = dict(handlers)
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._events: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._loop, name="catalogo-jobs", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)

    def submit(
        self,
        kind: str,
        params: dict[str, Any] | None = None,
        *,
        requested_by: str | None = None,
        allow_duplicate: bool = False,
    ) -> int:
        if kind not in self.handlers:
            raise JobError(f"tarea desconocida: {kind}")
        job_id = create_job(
            self.db_path,
            kind,
            params,
            requested_by=requested_by,
            allow_duplicate=allow_duplicate,
        )
        self._queue.put(job_id)
        return job_id

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            event = self._events.get(int(job_id))
        if event is not None:
            event.set()
        return cancel_job(self.db_path, job_id)

    def _loop(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id is None:
                break
            job = get_job(self.db_path, job_id)
            if job is None or job["state"] == "cancelled":
                continue
            handler = self.handlers.get(job["kind"])
            if handler is None:
                update_job(
                    self.db_path,
                    job_id,
                    state="error",
                    error=f"sin manejador para {job['kind']}",
                    finished=True,
                )
                continue
            event = threading.Event()
            with self._lock:
                self._events[int(job_id)] = event
            update_job(
                self.db_path,
                job_id,
                state="running",
                progress=0.0,
                message="iniciando",
                started=True,
            )
            ctx = JobContext(
                job_id=int(job_id),
                kind=job["kind"],
                params=job.get("params") or {},
                db_path=self.db_path,
                config=self.config,
                cancel_event=event,
            )
            try:
                result = handler(ctx)
                if ctx.cancelled():
                    update_job(
                        self.db_path,
                        job_id,
                        state="cancelled",
                        message="cancelada",
                        finished=True,
                    )
                else:
                    update_job(
                        self.db_path,
                        job_id,
                        state="ok",
                        progress=1.0,
                        message="terminada",
                        result=result,
                        finished=True,
                    )
            except JobCancelled:
                update_job(
                    self.db_path,
                    job_id,
                    state="cancelled",
                    message="cancelada",
                    finished=True,
                )
            except Exception as exc:
                update_job(
                    self.db_path,
                    job_id,
                    state="error",
                    message="error",
                    error=f"{exc}\n{traceback.format_exc(limit=4)}",
                    finished=True,
                )
            finally:
                with self._lock:
                    self._events.pop(int(job_id), None)
