"""Orquestador de workers. Sin systemd y sin activar por defecto.

Un ciclo:
  1. limpiar stream_leases viejos
  2. qbit-sync (solo lectura)
  3. abandon-idle (dry-run salvo --execute-abandon)
  4. move-completed (dry-run salvo --execute-moves)
  5. seeding: quitar forceStart a completos (dry-run salvo --execute-seeding)
  6. debrid-check de públicos (si hay RD_API_KEY)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

Handler = Callable[[], Any]


@dataclass
class CycleResult:
    ok: bool
    stages: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {"ok": self.ok, "stages": self.stages}


def run_once(
    *,
    cleanup: Handler | None = None,
    sync: Handler | None = None,
    library: Handler | None = None,
    clean: Handler | None = None,
    repair: Handler | None = None,
    reseed: Handler | None = None,
    abandon: Handler | None = None,
    moves: Handler | None = None,
    seeding: Handler | None = None,
    evict: Handler | None = None,
    debrid: Handler | None = None,
) -> CycleResult:
    stages: dict[str, Any] = {}
    ok = True
    for name, handler in (
        ("cleanup", cleanup),
        ("sync", sync),
        ("library", library),
        ("clean", clean),
        ("repair", repair),
        ("reseed", reseed),
        ("abandon", abandon),
        ("moves", moves),
        ("seeding", seeding),
        ("evict", evict),
        ("debrid", debrid),
    ):
        if handler is None:
            continue
        try:
            stages[name] = handler()
        except Exception as exc:
            ok = False
            stages[name] = {"error": str(exc)[:300]}
    return CycleResult(ok, stages)


def run_forever(
    *,
    cleanup: Handler | None = None,
    sync: Handler | None = None,
    library: Handler | None = None,
    clean: Handler | None = None,
    repair: Handler | None = None,
    reseed: Handler | None = None,
    abandon: Handler | None = None,
    moves: Handler | None = None,
    seeding: Handler | None = None,
    evict: Handler | None = None,
    debrid: Handler | None = None,
    interval: float = 300.0,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict[str, Any]], None] = print,
) -> None:
    while True:
        started = datetime.now(timezone.utc).isoformat()
        result = run_once(
            cleanup=cleanup, sync=sync, library=library, clean=clean,
            repair=repair, reseed=reseed, abandon=abandon, moves=moves,
            seeding=seeding, evict=evict, debrid=debrid,
        )
        emit({"started": started, **result.public()})
        if once:
            return
        sleep(max(5.0, interval))
