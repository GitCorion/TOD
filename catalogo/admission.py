"""Puerta de admisión de descargas.

Regla dura: antes de añadir un torrent se comprueba que:

  - el tracker tiene política explícita de seed,
  - el release está vivo y tiene peers,
  - hay espacio para el torrent completo + reserva,
  - no se supera el máximo activo del tracker,
  - no hay ya un compromiso para ese release.

Si pasa, se crea un `download_intent` con `seed_required_until` y
`target_ratio`. Ese registro es el compromiso que impide borrar antes de
cumplir. Sin esta fila, el backend no debe descargar.
"""

from __future__ import annotations

import shutil
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .policy import (
    TrackerPolicy,
    default_policy,
    ensure_default_policy,
    get_policy,
)

ACTIVE_STATES = ("approved", "active", "seeding")
DEFAULT_RESERVE_BYTES = 20 * 1024**3


@dataclass(frozen=True)
class AdmissionRequest:
    asset_key: str
    release_id: int | None
    tracker: str
    size_bytes: int | None
    health: str | None
    seeders: int | None
    policy: TrackerPolicy | None
    active_for_tracker: int
    already_active: bool
    disk_free_bytes: int
    reserve_bytes: int
    reserved_bytes: int = 0
    require_health: bool = True
    min_seeders: int = 1
    max_size_bytes: int = 0
    now: datetime | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    required_bytes: int
    available_bytes: int = 0
    seed_required_until: datetime | None = None
    target_ratio: float | None = None


@dataclass(frozen=True)
class AdmissionResult:
    key: str
    release_id: int | None
    tracker: str
    intent_id: int | None
    decision: AdmissionDecision
    size_bytes: int | None
    disk_free_bytes: int
    reserved_bytes: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "release_id": self.release_id,
            "tracker": self.tracker,
            "allowed": self.decision.allowed,
            "reason": self.decision.reason,
            "intent_id": self.intent_id,
            "size_bytes": self.size_bytes,
            "required_bytes": self.decision.required_bytes,
            "available_bytes": self.decision.available_bytes,
            "disk_free_bytes": self.disk_free_bytes,
            "reserved_bytes": self.reserved_bytes,
            "seed_required_until": (
                self.decision.seed_required_until.isoformat()
                if self.decision.seed_required_until
                else None
            ),
            "target_ratio": self.decision.target_ratio,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def decide(request: AdmissionRequest) -> AdmissionDecision:
    required = (request.size_bytes or 0) + max(0, request.reserve_bytes)
    available = request.disk_free_bytes - max(0, request.reserved_bytes)
    if request.release_id is None:
        return AdmissionDecision(False, "sin_release", required, available)
    if request.policy is None:
        return AdmissionDecision(False, "sin_politica", required, available)
    if not request.policy.enabled:
        return AdmissionDecision(False, "tracker_disabled", required, available)
    if not request.size_bytes or request.size_bytes <= 0:
        return AdmissionDecision(False, "sin_tamano", required, available)
    # El tamaño no bloquea: se penaliza en la selección/ordenación.

    if request.already_active:
        return AdmissionDecision(False, "ya_en_curso", required, available)
    if request.require_health and (request.health or "unknown") != "alive":
        return AdmissionDecision(
            False, f"health_{request.health or 'unknown'}", required, available
        )
    if (request.seeders or 0) < request.min_seeders:
        return AdmissionDecision(False, "sin_peers", required, available)
    if (
        request.policy.privacy != "public"
        and request.policy.min_seed_hours <= 0
        and request.policy.target_ratio <= 0
    ):
        return AdmissionDecision(
            False, "politica_sin_requisito", required, available
        )
    if available < required:
        return AdmissionDecision(False, "sin_espacio", required, available)
    if (
        request.policy.max_active is not None
        and request.policy.max_active > 0
        and request.active_for_tracker >= request.policy.max_active
    ):
        return AdmissionDecision(False, "max_active", required, available)

    current = request.now or _now()
    seed_until = (
        current + timedelta(hours=request.policy.min_seed_hours)
        if request.policy.min_seed_hours > 0
        else None
    )
    return AdmissionDecision(
        True,
        "admitido",
        required,
        available,
        seed_until,
        request.policy.target_ratio or None,
    )


def disk_free_bytes(path: str | Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return 0


def _release(
    conn: Any, asset_id: int, release_id: int | None, max_size_bytes: int = 0
) -> Any:
    if release_id is not None:
        row = conn.execute(
            "SELECT * FROM releases WHERE id = ? AND asset_id = ?",
            (release_id, asset_id),
        ).fetchone()
        return row
    return conn.execute(
        """
        SELECT * FROM releases
        WHERE asset_id = ?
        ORDER BY
            CASE health WHEN 'alive' THEN 0 WHEN 'unknown' THEN 1
                        WHEN 'stale' THEN 2 ELSE 3 END,
            CASE quality WHEN '4K' THEN 0 WHEN '1080p' THEN 1
                         WHEN '720p' THEN 2 ELSE 3 END,
            COALESCE(size_bytes, 999999999999) ASC,
            COALESCE(seeders, 0) DESC,
            id DESC
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone()


def _active_for_tracker(conn: Any, tracker: str) -> int:
    """Cuenta descargas activas, no semillas de 120 h ya completadas.

    Un compromiso `active`/`seeding` ya está materializado y no debe ocupar
    para siempre el cupo de descargas simultáneas del tracker. Solo cuentan
    los `approved` que aún no tienen torrent y los torrents que descargan.
    """
    intents = conn.execute(
        """
        SELECT COUNT(*) FROM seed_commitments i
        WHERE i.tracker = ?
          AND i.state = 'approved'
          AND NOT EXISTS (
              SELECT 1 FROM torrents t
              WHERE (i.info_hash IS NOT NULL AND t.info_hash = i.info_hash)
                 OR (i.release_id IS NOT NULL AND t.release_id = i.release_id)
          )
        """,
        (tracker,),
    ).fetchone()[0]
    torrents = conn.execute(
        """
        SELECT COUNT(*) FROM torrents t
        LEFT JOIN releases r ON r.id = t.release_id
        WHERE COALESCE(r.tracker, t.tracker) = ?
          AND t.category = 'ondemand'
          AND t.state IN ('waiting','downloading')
          AND lower(COALESCE(t.qbit_state, '')) NOT IN (
              'queueddl', 'queuedup', 'checkingdl', 'checkingup',
              'pauseddl', 'pausedup', 'stoppeddl', 'stoppedup', 'metadl'
          )
        """,
        (tracker,),
    ).fetchone()[0]
    return int(intents) + int(torrents)


def _reserved_bytes(conn: Any) -> int:
    """Bytes pendientes de descargar, no lo que ya está en disco.

    Un compromiso ``active`` con el torrent completo ya ocupa espacio real
    (contado por ``disk_usage``). Volver a reservarlo duplicaba el cálculo
    y bloqueaba nuevas descargas con ``sin_espacio``.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN c.state = 'approved' THEN c.size_bytes
                WHEN c.state = 'active' AND EXISTS (
                    SELECT 1 FROM torrents t
                    WHERE t.state IN ('waiting', 'downloading')
                      AND t.progress < 0.999
                      AND (
                          t.release_id = c.release_id
                          OR (
                              c.info_hash IS NOT NULL
                              AND c.info_hash <> ''
                              AND t.info_hash = c.info_hash
                          )
                      )
                ) THEN c.size_bytes
                ELSE 0
            END
        ), 0)
        FROM seed_commitments c
        WHERE c.state IN ('approved', 'active')
        """
    ).fetchone()
    return int(row[0] or 0)


def _already_active(conn: Any, release_id: int) -> bool:
    intent = conn.execute(
        "SELECT 1 FROM seed_commitments WHERE release_id = ? "
        "AND state IN ('approved','active','seeding') LIMIT 1",
        (release_id,),
    ).fetchone()
    if intent is not None:
        return True
    torrent = conn.execute(
        "SELECT 1 FROM torrents WHERE release_id = ? "
        "AND state IN ('waiting','downloading','complete','seeding') LIMIT 1",
        (release_id,),
    ).fetchone()
    return torrent is not None


def admit(
    db_path: str | Path,
    key: str,
    *,
    release_id: int | None = None,
    data_path: str | Path,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    require_health: bool = True,
    min_seeders: int = 1,
    max_size_bytes: int = 0,
    persist: bool = True,
    now: datetime | None = None,
    disk_free_override: int | None = None,
    active_for_tracker_override: int | None = None,
) -> AdmissionResult:
    from .identity import normalize_key

    current = now or _now()
    normalized = normalize_key(key) or key
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        asset = conn.execute(
            "SELECT * FROM media_items WHERE key = ?", (normalized,)
        ).fetchone()
        if asset is None:
            decision = AdmissionDecision(False, "asset_not_found", 0)
            return AdmissionResult(
                key, None, "", None, decision, None,
                disk_free_override
                if disk_free_override is not None
                else disk_free_bytes(data_path),
            )

        row = _release(conn, asset["id"], release_id, max_size_bytes)
        if row is None:
            decision = AdmissionDecision(False, "sin_release", 0)
            return AdmissionResult(
                key, release_id, "", None, decision, None,
                disk_free_override
                if disk_free_override is not None
                else disk_free_bytes(data_path),
            )
        failed = conn.execute(
            """
            SELECT 1 FROM stream_failures
            WHERE release_id = ?
              AND until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (int(row["id"]),),
        ).fetchone()
        if failed is not None:
            decision = AdmissionDecision(False, "stream_bad_swarm", 0)
            return AdmissionResult(
                key, int(row["id"]), row["tracker"] or "", None, decision,
                row["size_bytes"],
                disk_free_override
                if disk_free_override is not None
                else disk_free_bytes(data_path),
            )

        tracker = row["tracker"] or ""
        policy = get_policy(conn, tracker)
        if policy is None:
            policy = (
                ensure_default_policy(conn, tracker, row["privacy"])
                if persist
                else default_policy(tracker, row["privacy"])
            )
        free = (
            disk_free_override
            if disk_free_override is not None
            else disk_free_bytes(data_path)
        )
        reserved = _reserved_bytes(conn)
        request = AdmissionRequest(
            asset_key=normalized,
            release_id=row["id"],
            tracker=tracker,
            size_bytes=row["size_bytes"],
            health=row["health"],
            seeders=row["seeders"],
            policy=policy,
            active_for_tracker=(
                active_for_tracker_override
                if active_for_tracker_override is not None
                else _active_for_tracker(conn, tracker)
            ),
            already_active=_already_active(conn, row["id"]),
            disk_free_bytes=free,
            reserve_bytes=reserve_bytes,
            reserved_bytes=reserved,
            require_health=require_health,
            min_seeders=min_seeders,
            max_size_bytes=max_size_bytes,
            now=current,
        )
        decision = decide(request)

        intent_id = None
        if decision.allowed and persist:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO seed_commitments(
                        asset_id, release_id, tracker, info_hash, size_bytes,
                        state, reason, seed_required_until, target_ratio,
                        approved_at)
                    VALUES (?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?)
                    """,
                    (
                        asset["id"],
                        row["id"],
                        tracker,
                        row["info_hash"],
                        row["size_bytes"],
                        decision.reason,
                        decision.seed_required_until.isoformat()
                        if decision.seed_required_until
                        else None,
                        decision.target_ratio,
                        current.isoformat(),
                    ),
                )
                intent_id = cursor.lastrowid
                conn.commit()
            except Exception as exc:
                conn.rollback()
                decision = AdmissionDecision(
                    False, f"no_se_pudo_registrar:{exc.__class__.__name__}",
                    decision.required_bytes,
                )

        return AdmissionResult(
            key=normalized,
            release_id=row["id"],
            tracker=tracker,
            intent_id=intent_id,
            decision=decision,
            size_bytes=row["size_bytes"],
            disk_free_bytes=free,
            reserved_bytes=reserved,
        )


def list_intents(db_path: str | Path, limit: int = 50) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT i.*, a.key
            FROM seed_commitments i
            JOIN media_items a ON a.id = i.asset_id
            ORDER BY i.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def cancel_intent(
    db_path: str | Path, intent_id: int, reason: str = "cancelado"
) -> dict:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE seed_commitments
            SET state = 'cancelled', reason = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND state IN ('approved', 'active')
            """,
            (reason, intent_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM seed_commitments WHERE id = ?", (intent_id,)
        ).fetchone()
    return dict(row) if row else {}


def set_intent_state(
    db_path: str | Path, intent_id: int, state: str
) -> dict:
    allowed = {"approved", "active", "seeding", "done", "cancelled", "failed"}
    if state not in allowed:
        raise ValueError(f"estado inválido: {state}")
    column = {
        "active": "started_at",
        "seeding": "started_at",
        "done": "completed_at",
        "cancelled": "completed_at",
        "failed": "completed_at",
    }.get(state)
    with closing(connect(db_path)) as conn:
        if column:
            conn.execute(
                f"UPDATE seed_commitments SET state = ?, {column} = "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (state, intent_id),
            )
        else:
            conn.execute(
                "UPDATE seed_commitments SET state = ? WHERE id = ?",
                (state, intent_id),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM seed_commitments WHERE id = ?", (intent_id,)
        ).fetchone()
    return dict(row) if row else {}


def commit_until(db_path: str | Path, intent_id: int) -> datetime | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT seed_required_until FROM seed_commitments WHERE id = ?",
            (intent_id,),
        ).fetchone()
    if not row or not row["seed_required_until"]:
        return None
    return datetime.fromisoformat(
        row["seed_required_until"].replace("Z", "+00:00")
    )
