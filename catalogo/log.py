"""Logging de TOD.

Un solo formato para systemd y Docker, con nivel configurable por
`TOD_LOG_LEVEL` (INFO por defecto). Las tareas siguen guardando su estado en
la base; esto es para diagnosticar el proceso.
"""

from __future__ import annotations

from collections import deque
import logging
import os
import threading
from typing import Any

_CONFIGURED = False
_LOG_LOCK = threading.Lock()
_LOG_BUFFER: deque[dict[str, Any]] = deque(maxlen=500)


class _MemoryLogHandler(logging.Handler):
    """Mantiene los últimos registros en memoria para el visor web."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                message = f"{message}\n{formatter.formatException(record.exc_info)}"
            entry = {
                "ts": logging.Formatter(datefmt="%Y-%m-%dT%H:%M:%S%z").formatTime(
                    record, "%Y-%m-%dT%H:%M:%S%z"
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
            with _LOG_LOCK:
                _LOG_BUFFER.append(entry)
        except Exception:
            pass


def setup() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    raw = (os.environ.get("TOD_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, raw, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    root = logging.getLogger()
    if not any(isinstance(h, _MemoryLogHandler) for h in root.handlers):
        mem = _MemoryLogHandler()
        mem.setLevel(logging.DEBUG)
        root.addHandler(mem)
    _CONFIGURED = True


def recent_logs(
    *,
    limit: int = 200,
    level: str = "",
    q: str = "",
) -> list[dict[str, Any]]:
    """Devuelve los últimos registros en orden cronológico inverso."""
    with _LOG_LOCK:
        items = list(_LOG_BUFFER)
    lvl = (level or "").strip().upper()
    needle = (q or "").strip().lower()
    if lvl:
        items = [entry for entry in items if entry["level"] == lvl]
    if needle:
        items = [
            entry
            for entry in items
            if needle in entry["message"].lower()
            or needle in entry["logger"].lower()
        ]
    items.reverse()
    return items[: max(1, min(int(limit or 200), 500))]


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(f"tod.{name}")
