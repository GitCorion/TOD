"""Logging de TOD.

Un solo formato para systemd y Docker, con nivel configurable por
`TOD_LOG_LEVEL` (INFO por defecto). Las tareas siguen guardando su estado en
la base; esto es para diagnosticar el proceso.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


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
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(f"tod.{name}")
