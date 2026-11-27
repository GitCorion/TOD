"""Vigila que una fuente pueda alimentar el stream sin cortes.

No basta con que el tracker diga que hay seeds: si el peer conectado no
tiene las primeras piezas o la velocidad no llega al bitrate necesario,
el player se queda esperando. Este guardián mide buffer contiguo y
velocidad sostenida, marca la fuente como mala y cambia a la siguiente.
"""

from __future__ import annotations

import threading
import time
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from . import play_intents, seed_health
from .db import connect
from .resolver import Resolver
from .status import _buffer_required, _buffer_state

DEFAULT_INTERVAL = 5.0
DEFAULT_GRACE_SECONDS = 40.0
DEFAULT_SAFETY = 1.25
DEFAULT_FAILURE_HOURS = 2
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RUNTIME_SECONDS = 7200
LEASE_WINDOW_MINUTES = 5
DISCARD_DELETE_THRESHOLD = 0.05


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tracker_terminal_error(
    qbit: Any, info_hash: str, *, private: bool = False
) -> str | None:
    """Devuelve el código de error si el tracker rechaza el torrent."""
    trackers_fn = getattr(qbit, "trackers", None)
    if not callable(trackers_fn) or not info_hash:
        return None
    try:
        items = trackers_fn(info_hash) or []
    except Exception:
        return None
    msgs = [
        str(x.get("msg") or "").strip()
        for x in items
        if str(x.get("url") or "").startswith("http")
        and str(x.get("msg") or "").strip()
    ]
    if not msgs:
        return None
    verdict, _ = seed_health.classify(msgs)
    if verdict in ("client_not_allowed", "not_authorized"):
        return verdict
    if verdict == "unregistered" and private:
        return verdict
    return None


def block_release(
    db_path,
    release_id: int,
    asset_id: int | None,
    reason: str,
    *,
    hours: int = DEFAULT_FAILURE_HOURS,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    until = current + timedelta(hours=max(1, hours))
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO stream_failures(release_id, asset_id, reason, until)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(release_id) DO UPDATE SET
                asset_id = excluded.asset_id,
                reason = excluded.reason,
                until = excluded.until,
                created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (int(release_id), asset_id, reason, until.isoformat()),
        )
        conn.commit()


def failed_release_ids(
    db_path, *, asset_id: int | None = None, now: datetime | None = None
) -> set[int]:
    current = now or _now()
    query = (
        "SELECT release_id FROM stream_failures "
        "WHERE until > ?"
    )
    params: list[Any] = [current.isoformat()]
    if asset_id is not None:
        query += " AND asset_id = ?"
        params.append(int(asset_id))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(query, params).fetchall()
    return {int(row["release_id"]) for row in rows}


class StreamGuard:
    def __init__(
        self,
        db_path: str,
        play_service: Any,
        *,
        interval: float = DEFAULT_INTERVAL,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        safety: float = DEFAULT_SAFETY,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        nudge_hold: float = 2.0,
    ):
        self.db_path = db_path
        self.play_service = play_service
        self.interval = max(2.0, interval)
        self.grace_seconds = max(5.0, grace_seconds)
        self.safety = max(1.0, safety)
        self.max_attempts = max(1, max_attempts)
        self._samples: dict[str, list[tuple[float, int, float, float]]] = {}
        self._since: dict[str, float] = {}
        self._release: dict[str, int | None] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._busy_checked = 0.0
        self._last_switch: dict[str, float] = {}
        self._last_nudge: dict[str, float] = {}
        self._audio_ok: dict[str, bool | None] = {}
        self.nudge_hold = max(0.0, nudge_hold)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="catalogo-stream-guard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            except Exception as exc:
                print(f"stream-guard error: {exc}"[:300], flush=True)

    def run_once(self, *, now: datetime | None = None) -> dict:
        current = now or _now()
        threshold = (current - timedelta(minutes=LEASE_WINDOW_MINUTES)).isoformat()
        recent_start = (
            current - timedelta(minutes=15)
        ).isoformat()
        with closing(connect(self.db_path)) as conn:
            keys = [
                str(row["media_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT media_id FROM stream_leases
                    WHERE last_seen >= ?
                    """,
                    (threshold,),
                ).fetchall()
            ]
            keys.extend(
                str(row["key"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT a.key
                    FROM seed_commitments c
                    JOIN media_items a ON a.id = c.asset_id
                    JOIN torrents t ON t.release_id = c.release_id
                    WHERE c.state IN ('approved', 'active')
                      AND t.category = 'ondemand'
                      AND t.progress < 0.999
                      AND COALESCE(c.started_at, c.approved_at) >= ?
                    """,
                    (recent_start,),
                ).fetchall()
            )
            keys.extend(
                str(row["media_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT media_id FROM play_intents
                    WHERE created_at >= ? AND expires_at >= ?
                    """,
                    (recent_start, current.isoformat()),
                ).fetchall()
            )
        keys = list(dict.fromkeys(keys))
        self._busy = self._qbit_busy(current.timestamp())
        report: dict[str, Any] = {
            "keys": keys,
            "qbit_busy": self._busy,
            "bad": [],
            "switched": [],
            "deferred": [],
            "nudged": [],
            "errors": [],
        }
        for key in keys:
            try:
                self._check_key(key, current, report)
            except Exception as exc:
                report["errors"].append(f"{key}: {str(exc)[:180]}")
        return report

    def _qbit_busy(self, now_ts: float) -> bool:
        """No confundir una tormenta de checks/cola con un swarm muerto."""
        if now_ts - self._busy_checked < 60:
            return self._busy
        self._busy_checked = now_ts
        try:
            torrents = self.play_service.qbit_write.torrents()
        except Exception:
            self._busy = False
            return False
        checking = sum(
            1 for item in torrents
            if (item.get("state") or "").lower().startswith("checking")
        )
        queued = sum(
            1 for item in torrents
            if (item.get("state") or "").lower() in ("queueddl", "queuedup")
        )
        self._busy = checking >= 50 or queued >= 200
        return self._busy

    def _clear(self, key: str) -> None:
        self._samples.pop(key, None)
        self._since.pop(key, None)
        self._release.pop(key, None)
        self._audio_ok.pop(key, None)
        self._last_nudge.pop(key, None)

    def _maybe_nudge(
        self,
        key: str,
        info_hash: str,
        files: list[dict],
        file_index: int,
        samples: list[tuple[float, int, float, float]],
        speed: float,
        buffer_bytes: int,
        buffer_required: int,
        now_ts: float,
        report: dict[str, Any],
        *,
        progress: float = 0.0,
    ) -> None:
        """Re-pide el hueco que bloquea el prefijo contiguo (incluida la pieza 0).

        qBittorrent no expone control de piezas por la WebUI. Poner el
        fichero objetivo a prioridad 0 durante un par de segundos cancela
        las peticiones en vuelo; al restaurar, las piezas se vuelven a
        pedir a otros pares. El resto del pack no se toca y no se deja
        nada a medias.
        """
        has_download = buffer_bytes > 0 or speed > 0 or progress > 0.002
        if (
            len(samples) < 3
            or not has_download
            or buffer_bytes >= buffer_required
        ):
            return
        recent = samples[-3:]
        window = recent[-1][0] - recent[0][0]
        growth = recent[-1][1] - recent[0][1]
        progress_growth = recent[-1][3] - recent[0][3]
        if window < 8 or growth > 0:
            return
        if buffer_bytes == 0 and progress_growth > 0 and len(samples) < 5:
            return
        if now_ts - self._last_nudge.get(key, 0.0) < 30:
            return
        qbit = self.play_service.qbit_write
        setter = getattr(qbit, "set_file_priorities", None)
        if not callable(setter):
            return
        target = next(
            (
                item for item in files
                if int(item.get("index") or 0) == int(file_index)
            ),
            None,
        )
        if target is None:
            return
        priority = int(target.get("priority") or 1)
        if priority <= 0:
            priority = 1
        payload = {
            int(item.get("index") or 0): int(item.get("priority") or 0)
            for item in files
        }
        payload[int(file_index)] = 0
        self._last_nudge[key] = now_ts
        print(
            f"stream-nudge key={key} file={int(file_index)} "
            f"buffer={int(buffer_bytes)}/{int(buffer_required)} "
            f"speed={int(speed)}",
            flush=True,
        )
        error = None
        try:
            setter(info_hash, payload)
            if self.nudge_hold > 0:
                time.sleep(self.nudge_hold)
        except Exception as exc:
            error = str(exc)[:120]
        finally:
            payload[int(file_index)] = priority
            try:
                setter(info_hash, payload)
            except Exception as exc:
                error = error or str(exc)[:120]
        report.setdefault("nudged", []).append(
            {
                "key": key,
                "file_index": int(file_index),
                "buffer_bytes": int(buffer_bytes),
                "buffer_required": int(buffer_required),
                "error": error,
            }
        )

    def _record_swarm(
        self,
        release_id: int | None,
        tracker: str | None,
        *,
        connected_seeds: int,
        swarm_seeds: int,
        speed_bps: float,
        buffer_bytes: int,
        required_speed_bps: float,
        started_ok: bool = False,
        failure: str | None = None,
    ) -> None:
        if release_id is None:
            return
        with closing(connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO swarm_stats(
                    release_id, tracker, connected_seeds, swarm_seeds,
                    speed_bps, buffer_bytes, required_speed_bps,
                    started_ok, failures, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(release_id) DO UPDATE SET
                    tracker = excluded.tracker,
                    connected_seeds = excluded.connected_seeds,
                    swarm_seeds = excluded.swarm_seeds,
                    speed_bps = excluded.speed_bps,
                    buffer_bytes = excluded.buffer_bytes,
                    required_speed_bps = excluded.required_speed_bps,
                    started_ok = MAX(swarm_stats.started_ok,
                                     excluded.started_ok),
                    failures = swarm_stats.failures + excluded.failures,
                    last_seen = excluded.last_seen,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    int(release_id),
                    tracker,
                    int(connected_seeds),
                    int(swarm_seeds),
                    int(speed_bps),
                    int(buffer_bytes),
                    int(required_speed_bps),
                    1 if started_ok else 0,
                    1 if failure else 0,
                    _now().isoformat(),
                ),
            )
            conn.commit()

    def _local_file_path(
        self,
        asset_id: int,
        release_id: int | None,
        file_index: int,
        files: list[dict],
        live: dict[str, Any],
    ) -> str | None:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT local_path, library_path, rel_path
                FROM torrent_files
                WHERE asset_id = ? AND file_index = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(asset_id), int(file_index)),
            ).fetchone()
        if row is None:
            return None
        path = row["local_path"] or row["library_path"]
        if path:
            return str(path)
        rel_path = row["rel_path"]
        save_path = live.get("save_path")
        if rel_path and save_path:
            return str(Path(save_path) / rel_path)
        return None

    def _check_key(self, key: str, current: datetime, report: dict) -> None:
        resolver = Resolver(self.db_path)
        resolution = resolver.resolve(key)
        if resolution.source != "torrent":
            self._clear(key)
            return
        asset = resolver.asset(key)
        if asset is None:
            self._clear(key)
            return
        if resolution.release_id is not None:
            with closing(connect(self.db_path)) as conn:
                failed = conn.execute(
                    """
                    SELECT 1 FROM stream_failures
                    WHERE release_id = ?
                      AND until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (int(resolution.release_id),),
                ).fetchone()
            if failed is not None:
                self._clear(key)
                return
        with closing(connect(self.db_path)) as conn:
            media = conn.execute(
                "SELECT runtime_seconds FROM media_items WHERE id = ?",
                (int(asset["id"]),),
            ).fetchone()
        runtime = int(media["runtime_seconds"] or 0) if media else 0
        runtime = runtime or DEFAULT_RUNTIME_SECONDS

        info_hash = resolution.info_hash
        if not info_hash:
            with closing(connect(self.db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT t.info_hash
                    FROM torrents t
                    JOIN releases r ON r.id = t.release_id
                    WHERE r.asset_id = ?
                    ORDER BY t.id DESC LIMIT 1
                    """,
                    (int(asset["id"]),),
                ).fetchone()
            info_hash = row["info_hash"] if row else None
        if not info_hash:
            self._clear(key)
            return

        qbit = self.play_service.qbit_write
        direct = getattr(qbit, "torrent", None)
        live = None
        if callable(direct):
            try:
                live = direct(info_hash)
            except Exception:
                live = None
        if live is None:
            for torrent in qbit.torrents():
                if (torrent.get("hash") or "").lower() == info_hash.lower():
                    live = torrent
                    break
        if live is None:
            self._clear(key)
            return

        progress = float(live.get("progress") or 0)
        if progress >= 0.999:
            self._clear(key)
            return
        speed = float(live.get("dlspeed") or 0)
        try:
            files = qbit.files(info_hash)
        except Exception:
            files = []
        file_index = resolution.file_index or 0
        file_size = None
        for item in files:
            if int(item.get("index") or 0) == int(file_index):
                file_size = int(item.get("size") or 0) or None
                break
        if not file_size:
            self._clear(key)
            return
        buffer_bytes, buffer_required, tail_ready = _buffer_state(
            qbit, info_hash, files, file_index, file_size, runtime
        )
        if buffer_bytes is None:
            self._clear(key)
            return
        required_speed = file_size / max(1, runtime)
        release_id = resolution.release_id
        self._record_swarm(
            release_id,
            resolution.tracker,
            connected_seeds=int(live.get("num_seeds") or 0),
            swarm_seeds=int(live.get("num_complete") or 0),
            speed_bps=speed,
            buffer_bytes=buffer_bytes,
            required_speed_bps=required_speed,
        )
        if release_id is not None and self._release.get(key) != int(release_id):
            self._release[key] = int(release_id)
            self._since[key] = current.timestamp()
            self._samples[key] = []
        since = self._since.setdefault(key, current.timestamp())
        samples = self._samples.setdefault(key, [])
        samples.append(
            (current.timestamp(), int(buffer_bytes), speed, progress)
        )
        del samples[:-6]

        if buffer_bytes >= buffer_required:
            self._record_swarm(
                release_id,
                resolution.tracker,
                connected_seeds=int(live.get("num_seeds") or 0),
                swarm_seeds=int(live.get("num_complete") or 0),
                speed_bps=speed,
                buffer_bytes=buffer_bytes,
                required_speed_bps=required_speed,
                started_ok=True,
            )
            return

        self._maybe_nudge(
            key,
            info_hash,
            files,
            int(file_index),
            samples,
            speed,
            int(buffer_bytes),
            int(buffer_required),
            current.timestamp(),
            report,
            progress=progress,
        )

        elapsed = current.timestamp() - since
        if elapsed < self.grace_seconds or len(samples) < 2:
            return

        recent = samples[-4:]
        window = max(1.0, recent[-1][0] - recent[0][0])
        growth = recent[-1][1] - recent[0][1]
        avg_speed = sum(item[2] for item in recent) / len(recent)
        progress_growth = recent[-1][3] - recent[0][3]
        qbit_state = str(live.get("state") or "").lower()
        connected_seeds = int(live.get("num_seeds") or 0)
        swarm_seeds = int(live.get("num_complete") or 0)
        num_leechs = int(live.get("num_leechs") or 0)
        has_peers = (connected_seeds + num_leechs + swarm_seeds) > 0
        making_progress = avg_speed > 0 or progress_growth > 0 or growth > 0
        zombie_stalled = (
            elapsed >= self.grace_seconds * 3.0
            and buffer_bytes < buffer_required
            and avg_speed < 64 * 1024
            and growth <= 0
            and progress_growth <= 0
        )

        tracker_err = _tracker_terminal_error(
            qbit, info_hash, private=bool(live.get("is_private"))
        )
        if tracker_err:
            reason = f"tracker_{tracker_err}"
        elif qbit_state in ("error", "missingfiles"):
            reason = "torrent_error"
        elif (
            not has_peers
            and not making_progress
            and buffer_bytes == 0
            and all(item[1] == 0 for item in recent)
        ):
            reason = "no_contiguous_buffer"
        elif zombie_stalled:
            reason = "buffer_stalled"
        else:
            reason = None
        if reason is None:
            return
        if self._busy:
            report["deferred"].append(
                {
                    "key": key,
                    "release_id": release_id,
                    "reason": "qbit_busy",
                    "detail": reason,
                }
            )
            return
        now_ts = current.timestamp()
        if now_ts - self._last_switch.get(key, 0.0) < 60:
            report["deferred"].append(
                {
                    "key": key,
                    "release_id": release_id,
                    "reason": "switch_cooldown",
                    "detail": reason,
                }
            )
            return
        self._last_switch[key] = now_ts
        self._record_swarm(
            release_id,
            resolution.tracker,
            connected_seeds=int(live.get("num_seeds") or 0),
            swarm_seeds=int(live.get("num_complete") or 0),
            speed_bps=avg_speed,
            buffer_bytes=buffer_bytes,
            required_speed_bps=required_speed,
            failure=reason,
        )

        report["bad"].append(
            {
                "key": key,
                "release_id": release_id,
                "reason": reason,
                "progress": round(progress, 4),
                "speed_bps": int(avg_speed),
                "required_speed_bps": int(required_speed),
                "buffer_bytes": int(buffer_bytes),
                "buffer_required": int(buffer_required),
                "tail_ready": bool(tail_ready),
                "window_s": round(window, 1),
            }
        )
        self._switch(
            key=key,
            asset_id=int(asset["id"]),
            release_id=release_id,
            info_hash=info_hash,
            progress=progress,
            reason=reason,
            report=report,
        )
        self._clear(key)

    def _switch(
        self,
        *,
        key: str,
        asset_id: int,
        release_id: int | None,
        info_hash: str,
        progress: float,
        reason: str,
        report: dict,
    ) -> None:
        if release_id is None:
            return
        block_release(
            self.db_path, int(release_id), asset_id, reason
        )
        qbit = self.play_service.qbit_write
        under_threshold = float(progress or 0.0) <= DISCARD_DELETE_THRESHOLD
        deleted = False
        if under_threshold:
            delete_fn = getattr(qbit, "delete", None)
            try:
                if callable(delete_fn):
                    delete_fn(info_hash, delete_files=True)
                    deleted = True
                else:
                    qbit.pause(info_hash)
            except Exception as exc:
                report["errors"].append(
                    f"{key}: no se pudo eliminar {str(exc)[:120]}"
                )
                return
        else:
            # Superó el 5 %: no borrar ni pausar; quitar force_start para
            # que siga descargando/sembrando en segundo plano sin H&R.
            try:
                force = getattr(qbit, "set_force_start", None)
                if callable(force):
                    force(info_hash, False)
            except Exception:
                pass

        with closing(connect(self.db_path)) as conn:
            torrent = conn.execute(
                "SELECT id FROM torrents WHERE info_hash = ?", (info_hash,)
            ).fetchone()
            torrent_id = int(torrent["id"]) if torrent else None
            if under_threshold:
                if torrent_id is not None:
                    if deleted:
                        conn.execute(
                            "UPDATE torrents SET state = 'removed', "
                            "qbit_state = 'removed' WHERE id = ?",
                            (torrent_id,),
                        )
                        conn.execute(
                            "UPDATE torrent_files SET state = 'evicted', "
                            "local_path = NULL WHERE torrent_id = ? "
                            "AND COALESCE(storage_location, 'LOCAL_DISK') <> '1FICHIER'",
                            (torrent_id,),
                        )
                    else:
                        conn.execute(
                            "UPDATE torrents SET state = 'paused', "
                            "qbit_state = 'paused' WHERE id = ?",
                            (torrent_id,),
                        )
                conn.execute(
                    """
                    UPDATE seed_commitments
                    SET state = 'cancelled', reason = ?,
                        completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE state IN ('approved', 'active')
                      AND (
                          release_id = ? OR info_hash = ?
                      )
                    """,
                    (reason, int(release_id), info_hash),
                )
            conn.execute(
                """
                UPDATE media_items SET active_file_id = NULL, status = 'COLD'
                WHERE id = ? AND active_file_id IN (
                    SELECT id FROM torrent_files WHERE release_id = ?
                )
                """,
                (asset_id, int(release_id)),
            )
            conn.execute(
                """
                UPDATE media_items
                SET stream_generation = stream_generation + 1,
                    stream_switched_at = ?,
                    stream_switch_reason = ?
                WHERE id = ?
                """,
                (_now().isoformat(), reason, asset_id),
            )
            conn.commit()

        failures = failed_release_ids(
            self.db_path, asset_id=asset_id
        )
        if len(failures) > self.max_attempts:
            report["errors"].append(
                f"{key}: demasiados intentos ({len(failures)})"
            )
            return
        play_intents.create(
            self.db_path, key, source="manual"
        )
        try:
            self.play_service.ensure(key)
        except Exception as exc:
            report["errors"].append(
                f"{key}: fallback falló: {str(exc)[:160]}"
            )
            return
        report["switched"].append(
            {
                "key": key,
                "from_release": release_id,
                "reason": reason,
                "attempts": len(failures),
            }
        )
        print(
            f"stream-guard switch key={key} from={release_id} "
            f"reason={reason} attempts={len(failures)}",
            flush=True,
        )
