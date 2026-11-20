"""Bloquea/desbloquea un tracker completo para descargas on-demand.

Se usa cuando el índice devuelve páginas de login, torrents retirados o un
HTTP 404/500 sistemático: buscar funciona, pero descargar no. No se tocan
los compromisos ya activos (se siguen sembrando); solo se impide que el
catálogo elija ese tracker para nuevas descargas.

`blocked` es un valor de `releases.health`, así que el resolver y el
exportador de `.strm` lo ignoran. La política queda deshabilitada, lo que
hace que la admisión rechace el tracker con `tracker_disabled`.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from .db import connect


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_tracker_blocked(
    db_path,
    tracker: str,
    *,
    blocked: bool = True,
    reason: str = "downloads_broken",
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    current = now or _now()
    report: dict = {
        "tracker": tracker,
        "action": "block" if blocked else "unblock",
        "releases": 0,
        "policy": "missing",
        "dry_run": dry_run,
    }
    with closing(connect(db_path)) as conn:
        if blocked:
            rows = conn.execute(
                "SELECT COUNT(*) FROM releases "
                "WHERE tracker = ? AND health <> 'blocked'",
                (tracker,),
            ).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT COUNT(*) FROM releases "
                "WHERE tracker = ? AND health = 'blocked'",
                (tracker,),
            ).fetchone()[0]
        report["releases"] = int(rows or 0)

        policy = conn.execute(
            "SELECT enabled FROM tracker_policies WHERE tracker = ?",
            (tracker,),
        ).fetchone()
        if policy is not None:
            report["policy"] = "disabled" if blocked else "enabled"

        if dry_run:
            return report

        if blocked:
            conn.execute(
                """
                UPDATE releases
                SET health = 'blocked', health_checked_at = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ? AND health <> 'blocked'
                """,
                (current.isoformat(), tracker),
            )
            conn.execute(
                """
                UPDATE tracker_policies
                SET enabled = 0,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ?
                """,
                (tracker,),
            )
        else:
            conn.execute(
                """
                UPDATE releases
                SET health = 'unknown', health_checked_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ? AND health = 'blocked'
                """,
                (tracker,),
            )
            conn.execute(
                """
                UPDATE tracker_policies
                SET enabled = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ?
                """,
                (tracker,),
            )
        conn.commit()
        report["reason"] = reason
    return report
