"""Siembra mínima con force-start.

TOD garantiza el mínimo pactado (horas y ratio): mientras no se cumpla,
pone el torrent en force-start para que qBittorrent no lo pare por cola o
límites y, si estaba pausado, lo reanuda. Al cumplirse, lo suelta y
qBittorrent decide qué hacer con el torrent y sus ficheros. TOD no borra nada.

El reloj de siembra empieza cuando la descarga termina, no cuando empieza:
`seed_started_at` sale de qBittorrent (`seeding_time`) y, si aún no hay dato,
de `completed_at`. Contar desde la descarga soltaba el torrent antes de
cumplir y arriesgaba hit-and-run.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

from .db import connect
from .policy import get_policy, parse_time
from .seed_health import classify
from .seed_settings import load as load_seed_settings

FORCED_STATES = ("forcedup", "forceddl")
ACTIVE_STATES = ("approved", "active", "seeding")
PAUSED_STATES = ("paused", "stopped")
ERROR_STATES = ("missing", "error")


class SeedingClient(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...
    def set_force_start(self, info_hash: str, value: bool = True) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_start(commitment: Any, torrent: Any) -> datetime | None:
    """Inicio real de siembra: primero qBittorrent, luego la finalización."""
    for value in (
        commitment.get("seed_started_at"),
        commitment.get("torrent_seed_started_at"),
        commitment.get("torrent_completed_at"),
        torrent.get("seed_started_at"),
        torrent.get("completed_at"),
    ):
        parsed = parse_time(value)
        if parsed is not None:
            return parsed
    return None


def _deadline(
    commitment: Any, policy: Any, seed_start: datetime | None
) -> datetime | None:
    until = parse_time(commitment["seed_required_until"])
    if until is not None:
        return until
    if seed_start is None or policy is None:
        return None
    return seed_start + timedelta(hours=max(0, policy.min_seed_hours))


def _tracker_verdict(
    client: SeedingClient, info_hash: str, release_url: str | None
) -> tuple[str | None, str | None, bool]:
    """Solo mira el tracker del release, no los públicos añadidos al .torrent.

    Un mismo .torrent puede llevar decenas de trackers públicos; sus
    mensajes ("Torrent not registered" en uno ajeno) no dicen nada del
    tracker real y producirían falsos avisos.

    Devuelve (veredicto, mensaje, comprobado). `comprobado` indica que se
    encontró el tracker del release y su estado se puede dar por bueno.
    """
    check = getattr(client, "trackers", None)
    if not callable(check):
        return None, None, False
    base = (urlsplit(release_url or "").hostname or "").lower()
    if base.startswith("www."):
        base = base[4:]
    if not base:
        return None, None, False
    try:
        entries = check(info_hash) or []
    except Exception:
        return None, None, False
    messages: list[str] = []
    matched = False
    for entry in entries:
        url = str(entry.get("url") or "")
        if url.startswith("**"):
            # DHT/PEX/LSD no son trackers reales.
            continue
        host = (urlsplit(url).hostname or "").lower()
        if not (host == base or host.endswith("." + base)):
            continue
        matched = True
        message = str(entry.get("msg") or "").strip()
        if message:
            messages.append(message)
        elif int(entry.get("status") or 0) == 4:
            messages.append("el tracker no responde")
    if not matched:
        return None, None, False
    verdict, message = classify(messages)
    return verdict, message, True


def normalize_completed(
    db_path: str,
    client: SeedingClient,
    *,
    limit: int = 50,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    seed = load_seed_settings(db_path)
    report: dict[str, Any] = {
        "checked": 0,
        "forced": [],
        "resumed": [],
        "released": [],
        "kept": [],
        "cancelled": 0,
        "skipped": [],
        "problems": [],
        "errors": [],
        "force": seed["force"],
        "dry_run": dry_run,
    }
    # Un intento que no llegó a crear torrent no debe ocupar una plaza de
    # admisión para siempre.
    cutoff = (current - timedelta(minutes=10)).isoformat()
    with closing(connect(db_path)) as conn:
        cur = conn.execute(
            """
            UPDATE seed_commitments SET state='cancelled',
                reason='no_torrent', completed_at=?
            WHERE state='approved'
              AND COALESCE(approved_at, created_at) < ?
              AND NOT EXISTS (
                  SELECT 1 FROM torrents t
                  WHERE (seed_commitments.info_hash IS NOT NULL
                         AND t.info_hash = seed_commitments.info_hash)
                     OR (seed_commitments.release_id IS NOT NULL
                         AND t.release_id = seed_commitments.release_id)
              )
            """,
            (current.isoformat(), cutoff),
        )
        conn.commit()
        report["cancelled"] = int(cur.rowcount)

    if not seed["force"]:
        report["skipped"].append("force_disabled")
        return report

    live = {
        (torrent.get("hash") or "").lower(): torrent
        for torrent in client.torrents()
        if torrent.get("hash")
    }
    with closing(connect(db_path)) as conn:
        commitments = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.*, t.info_hash AS torrent_hash,
                       t.ratio AS torrent_ratio, t.state AS torrent_state,
                       t.seed_started_at AS torrent_seed_started_at,
                       t.completed_at AS torrent_completed_at,
                       r.info_url AS release_info_url
                FROM seed_commitments c
                LEFT JOIN torrents t ON t.info_hash = c.info_hash
                LEFT JOIN releases r ON r.id = c.release_id
                WHERE c.state IN ('approved', 'active', 'seeding')
                ORDER BY c.id
                """
            ).fetchall()
        ]
        for commitment in commitments:
            if commitment.get("info_hash") or not commitment.get("release_id"):
                continue
            row = conn.execute(
                "SELECT t.info_hash, t.seed_started_at, t.completed_at, "
                "t.ratio, t.state, r.info_url "
                "FROM torrents t LEFT JOIN releases r ON r.id = t.release_id "
                "WHERE t.release_id = ? "
                "ORDER BY t.id DESC LIMIT 1",
                (int(commitment["release_id"]),),
            ).fetchone()
            if row is not None:
                commitment["info_hash"] = row["info_hash"]
                commitment["torrent_seed_started_at"] = row["seed_started_at"]
                commitment["torrent_completed_at"] = row["completed_at"]
                commitment["torrent_ratio"] = row["ratio"]
                commitment["torrent_state"] = row["state"]
                commitment["release_info_url"] = row["info_url"]

    for commitment in commitments:
        if report["checked"] >= limit:
            break
        info_hash = (
            commitment.get("info_hash")
            or commitment.get("torrent_hash")
            or ""
        ).lower()
        torrent = live.get(info_hash)
        if torrent is None:
            # Sin torrent vivo: qbit-sync decidirá si el compromiso se cerró.
            continue
        if float(torrent.get("progress") or 0) < 1:
            report["skipped"].append("incomplete")
            continue
        report["checked"] += 1
        with closing(connect(db_path)) as conn:
            policy = get_policy(conn, commitment["tracker"])

        state = (torrent.get("state") or "").lower()
        if any(marker in state for marker in ERROR_STATES):
            report["problems"].append(
                {
                    "info_hash": info_hash,
                    "tracker": commitment["tracker"],
                    "verdict": "torrent_roto",
                    "message": f"qBittorrent marca {state}; no puede sembrar",
                }
            )
            continue

        seed_start = _seed_start(commitment, torrent)
        if seed_start is None:
            # Descarga recién completada sin dato de qBittorrent: el reloj
            # empieza ahora, nunca antes de terminar.
            seed_start = current
        deadline = _deadline(commitment, policy, seed_start)
        min_hours = int(policy.min_seed_hours) if policy else 0
        unknown_start = deadline is None and min_hours > 0
        target = float(
            commitment.get("target_ratio")
            or (policy.target_ratio if policy else 0)
            or 0
        )
        ratio = float(
            torrent.get("ratio") or commitment.get("torrent_ratio") or 0
        )
        met = (
            not unknown_start
            and (deadline is None or current >= deadline)
            and (target <= 0 or ratio >= target)
        )
        forced = state in FORCED_STATES
        paused = state.startswith(PAUSED_STATES)
        item = {
            "info_hash": info_hash,
            "tracker": commitment["tracker"],
            "ratio": round(ratio, 3),
            "target_ratio": target,
            "seed_started_at": seed_start.isoformat(),
            "seed_required_until": deadline.isoformat() if deadline else None,
            "remaining_seconds": (
                max(0, int((deadline - current).total_seconds()))
                if deadline is not None
                else None
            ),
        }

        verdict = message = None
        checked_tracker = False
        if not dry_run:
            verdict, message, checked_tracker = _tracker_verdict(
                client, info_hash, commitment.get("release_info_url")
            )
            if verdict is not None:
                item["verdict"] = verdict
                report["problems"].append(
                    {
                        "info_hash": info_hash,
                        "tracker": commitment["tracker"],
                        "verdict": verdict,
                        "message": (message or "")[:300],
                    }
                )
            if checked_tracker:
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE seed_commitments SET
                            tracker_verdict = ?,
                            tracker_message = ?,
                            tracker_checked_at = ?
                        WHERE id = ?
                        """,
                        (verdict, (message or "")[:300], current.isoformat(),
                         int(commitment["id"])),
                    )
                    conn.commit()

        if dry_run:
            report["released" if met else "kept"].append(item)
            continue
        try:
            if met:
                if forced:
                    client.set_force_start(info_hash, False)
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE seed_commitments SET state='done',
                            seed_started_at=COALESCE(seed_started_at, ?),
                            completed_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (seed_start.isoformat(), int(commitment["id"])),
                    )
                    conn.commit()
                report["released"].append(item)
            else:
                resume = getattr(client, "resume", None)
                if paused and callable(resume):
                    resume(info_hash)
                    report["resumed"].append(info_hash)
                if not forced:
                    client.set_force_start(info_hash, True)
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE seed_commitments SET
                            state='seeding',
                            seed_started_at=COALESCE(seed_started_at, ?),
                            seed_required_until=COALESCE(seed_required_until, ?),
                            started_at=COALESCE(started_at, ?)
                        WHERE id = ?
                        """,
                        (
                            seed_start.isoformat(),
                            deadline.isoformat() if deadline else None,
                            seed_start.isoformat(),
                            int(commitment["id"]),
                        ),
                    )
                    conn.commit()
                report["forced" if not forced else "kept"].append(item)
        except Exception as exc:
            report["errors"].append(f"{info_hash[:12]}: {str(exc)[:200]}")
    return report
