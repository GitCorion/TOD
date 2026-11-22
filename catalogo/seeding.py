"""Siembra mínima con force-start.

TOD garantiza el mínimo pactado (horas y ratio): mientras no se cumpla,
pone el torrent en force-start para que qBittorrent no lo pare por cola o
límites. Al cumplirse, lo suelta y qBittorrent decide qué hacer con el
torrent y sus ficheros. TOD no borra nada.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .db import connect
from .policy import get_policy, parse_time
from .seed_settings import load as load_seed_settings

FORCED_STATES = ("forcedup", "forceddl")
ACTIVE_STATES = ("approved", "active", "seeding")


class SeedingClient(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...
    def set_force_start(self, info_hash: str, value: bool = True) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline(commitment: Any, policy: Any) -> datetime | None:
    until = parse_time(commitment["seed_required_until"])
    if until is not None:
        return until
    started = parse_time(
        commitment.get("started_at") or commitment.get("seed_started_at")
    )
    if started is None or policy is None:
        return None
    return started + timedelta(hours=max(0, policy.min_seed_hours))


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
        "released": [],
        "kept": [],
        "cancelled": 0,
        "skipped": [],
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
                       t.ratio AS torrent_ratio, t.state AS torrent_state
                FROM seed_commitments c
                LEFT JOIN torrents t ON t.info_hash = c.info_hash
                WHERE c.state IN ('approved', 'active', 'seeding')
                ORDER BY c.id
                """
            ).fetchall()
        ]
        for commitment in commitments:
            if commitment.get("info_hash") or not commitment.get("release_id"):
                continue
            row = conn.execute(
                "SELECT info_hash FROM torrents WHERE release_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (int(commitment["release_id"]),),
            ).fetchone()
            if row is not None:
                commitment["info_hash"] = row["info_hash"]

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
        deadline = _deadline(commitment, policy)
        target = float(
            commitment.get("target_ratio")
            or (policy.target_ratio if policy else 0)
            or 0
        )
        ratio = float(torrent.get("ratio") or commitment.get("torrent_ratio") or 0)
        met = (deadline is None or current >= deadline) and (
            target <= 0 or ratio >= target
        )
        state = (torrent.get("state") or "").lower()
        forced = state in FORCED_STATES
        item = {
            "info_hash": info_hash,
            "tracker": commitment["tracker"],
            "ratio": round(ratio, 3),
            "target_ratio": target,
            "seed_required_until": deadline.isoformat() if deadline else None,
        }
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
                            completed_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (int(commitment["id"]),),
                    )
                    conn.commit()
                report["released"].append(item)
            else:
                if not forced:
                    client.set_force_start(info_hash, True)
                with closing(connect(db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE seed_commitments SET
                            state='seeding',
                            started_at=COALESCE(started_at, ?)
                        WHERE id = ?
                        """,
                        (current.isoformat(), int(commitment["id"])),
                    )
                    conn.commit()
                report["forced" if not forced else "kept"].append(item)
        except Exception as exc:
            report["errors"].append(f"{info_hash[:12]}: {str(exc)[:200]}")
    return report
