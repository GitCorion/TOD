"""Preferencias de idioma y reglas de siembra."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import language_settings, policy, seed_settings
from .db import connect
from .errors import WebError


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle(
    app: Any,
    parts: list[str],
    params: dict[str, str],
    body: dict[str, Any],
    *,
    client_ip: str = "",
    mutate: bool = False,
) -> tuple[int, Any] | None:
    db = app.db_path
    if parts == ["language"]:
        if not mutate:
            return 200, {
                    "preference": language_settings.load(db),
                    "choices": list(language_settings.CHOICES),
                }
        try:
            value = language_settings.save(
                db, str(body.get("preference") or "")
            )
        except ValueError as exc:
            raise WebError(400, str(exc))
        app.audit("web.language", payload={"preference": value})
        return 200, {"preference": value}

    if parts == ["seed"]:
        if not mutate:
            with closing(connect(db)) as conn:
                known = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT r.tracker, r.privacy, COUNT(*) AS releases
                        FROM releases r
                        WHERE r.tracker IS NOT NULL AND r.tracker <> ''
                        GROUP BY r.tracker, r.privacy
                        ORDER BY r.tracker
                        """
                    ).fetchall()
                ]
            return 200, {
                    "defaults": seed_settings.load(db),
                    "policies": policy.list_policies(db),
                    "known": known,
                }
        try:
            values = seed_settings.save(
                db,
                min_hours=body.get("min_hours"),
                ratio=body.get("ratio"),
                force=body.get("force"),
                allow_delete=body.get("allow_delete"),
                keep_until_library=body.get("keep_until_library"),
                keep_max_hours=body.get("keep_max_hours"),
                arr_fallback=body.get("arr_fallback"),
            )
        except (TypeError, ValueError) as exc:
            raise WebError(400, str(exc)[:200])
        app.audit("web.seed_settings", payload=values)
        return 200, {"defaults": values}

    if parts == ["seed", "policies"]:
        if not mutate:
            raise WebError(405, "usa POST")
        tracker = str(body.get("tracker") or "").strip()
        if not tracker:
            raise WebError(400, "falta tracker")
        existing = next(
            (
                item
                for item in policy.list_policies(db)
                if item.get("tracker") == tracker
            ),
            {},
        )
        privacy = str(
            body.get("privacy") or existing.get("privacy") or "private"
        )
        if privacy not in ("private", "public"):
            raise WebError(400, "privacidad inválida")
        try:
            item = policy.TrackerPolicy(
                tracker=tracker,
                min_seed_hours=max(
                    0, _int(body.get("min_seed_hours"), 72)
                ),
                target_ratio=max(
                    0.0, float(body.get("target_ratio") or 0)
                ),
                max_active=existing.get("max_active"),
                allow_partial=bool(existing.get("allow_partial")),
                keep_for_bonus=bool(existing.get("keep_for_bonus", True)),
                privacy=privacy,
                enabled=bool(body.get("enabled", True)),
                abandon_under_percent=int(
                    existing.get("abandon_under_percent") or 0
                ),
                abandon_action=str(
                    existing.get("abandon_action") or "keep"
                ),
                notes="Editada desde Configuración → Siembra.",
            )
            policy.upsert_policy(db, item)
            with closing(connect(db)) as conn:
                policy.reconcile_privacy(conn, tracker, item.privacy)
                conn.commit()
        except (TypeError, ValueError) as exc:
            raise WebError(400, str(exc)[:200])
        app.audit(
            "web.seed_policy",
            payload={
                "tracker": tracker,
                "min_seed_hours": item.min_seed_hours,
                "target_ratio": item.target_ratio,
                "enabled": item.enabled,
            },
        )
        return 200, {
                "tracker": tracker,
                "min_seed_hours": item.min_seed_hours,
                "target_ratio": item.target_ratio,
                "enabled": item.enabled,
            }
    return None
