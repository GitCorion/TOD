"""Preferencias de idioma y reglas de siembra."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from . import language_settings, naming, policy, profiles, seed_settings
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

    if parts == ["profiles"]:
        if not mutate:
            return 200, {
                "items": profiles.list_profiles(db),
                "custom_formats": profiles.list_custom_formats(db),
                "restrictions": profiles.list_restrictions(db),
                "qualities": list(profiles.DEFAULT_QUALITIES),
            }
        try:
            saved = profiles.save_profile(db, body)
        except ValueError as exc:
            raise WebError(400, str(exc))
        app.audit("web.profile_save", payload={"profile": saved.get("id"), "name": saved.get("name")})
        return 201, {"profile": saved}

    if len(parts) == 2 and parts[0] == "profiles":
        pid = _int(parts[1], 0)
        if pid <= 0:
            raise WebError(400, "perfil inválido")
        if not mutate:
            item = profiles.get_profile(db, pid)
            if not item:
                raise WebError(404, "perfil no encontrado")
            return 200, item
        if body.get("_delete"):
            try:
                deleted = profiles.delete_profile(db, pid)
            except ValueError as exc:
                raise WebError(400, str(exc))
            if not deleted:
                raise WebError(404, "perfil no encontrado")
            app.audit("web.profile_delete", payload={"profile": pid})
            return 200, {"deleted": True, "id": pid}
        try:
            saved = profiles.save_profile(db, body, profile_id=pid)
        except ValueError as exc:
            raise WebError(400, str(exc))
        app.audit("web.profile_save", payload={"profile": pid, "name": saved.get("name")})
        return 200, {"profile": saved}

    if parts == ["custom-formats"]:
        if not mutate:
            return 200, {"items": profiles.list_custom_formats(db)}
        try:
            saved = profiles.save_custom_format(db, body)
        except ValueError as exc:
            raise WebError(400, str(exc))
        return 201, {"format": saved}

    if len(parts) == 2 and parts[0] == "custom-formats":
        fid = _int(parts[1], 0)
        if fid <= 0:
            raise WebError(400, "formato inválido")
        if body.get("_delete") and mutate:
            return 200, {"deleted": profiles.delete_custom_format(db, fid)}
        if mutate:
            try:
                return 200, {"format": profiles.save_custom_format(db, body, format_id=fid)}
            except ValueError as exc:
                raise WebError(400, str(exc))

    if parts == ["restrictions"]:
        if not mutate:
            return 200, {"items": profiles.list_restrictions(db)}
        saved = profiles.save_restriction(db, body)
        return 201, {"restriction": saved}

    if len(parts) == 2 and parts[0] == "restrictions":
        rid = _int(parts[1], 0)
        if rid <= 0:
            raise WebError(400, "regla inválida")
        if body.get("_delete") and mutate:
            return 200, {"deleted": profiles.delete_restriction(db, rid)}
        if mutate:
            return 200, {"restriction": profiles.save_restriction(db, body, restriction_id=rid)}


    # --- Naming templates ---
    if parts == ["naming"]:
        if not mutate:
            cfg = naming.load_naming_settings(db)
            examples = naming.preview_examples(cfg)
            return 200, {"settings": cfg, "examples": examples}
        try:
            saved = naming.save_naming_settings(db, body)
            examples = naming.preview_examples(saved)
        except ValueError as exc:
            raise WebError(400, str(exc))
        return 200, {"settings": saved, "examples": examples}

    if parts == ["naming", "preview"]:
        cfg = dict(naming.load_naming_settings(db))
        for key in cfg:
            if key in body:
                cfg[key] = body[key]
        return 200, {"examples": naming.preview_examples(cfg)}

    if parts == ["naming", "reorganize"]:
        if not mutate:
            raise WebError(405, "usa POST")
        out_dir = app.config.catalog_library
        kind_filter = str(body.get("kind") or "") or None
        tmdb_filter = int(body.get("tmdb")) if body.get("tmdb") else None
        if body.get("execute"):
            result = naming.apply_reorganize(db, out_dir, kind=kind_filter, tmdb=tmdb_filter)
            if result.get("moved"):
                from . import jellyfin, strm_inventory
                strm_inventory.invalidate(out_dir)
                jellyfin.notify_updated(app.config, result.get("affected_paths", [])[:500])
            app.audit("web.naming_reorganize", payload={"moved": result.get("moved", 0), "planned": result.get("planned", 0)})
            return 200, result
        moves = naming.plan_reorganize(db, out_dir, kind=kind_filter, tmdb=tmdb_filter)
        return 200, {"planned": len(moves), "moves": moves[:500]}

    # --- Root Folders ---
    if parts == ["root-folders"]:
        if not mutate:
            return 200, {"items": naming.list_root_folders(db)}
        try:
            saved = naming.save_root_folder(
                db,
                name=str(body.get("name") or "").strip(),
                kind=str(body.get("kind") or "any"),
                path=str(body.get("path") or "").strip(),
                is_default=bool(body.get("is_default")),
            )
        except ValueError as exc:
            raise WebError(400, str(exc))
        return 201, {"root_folder": saved}

    if len(parts) == 2 and parts[0] == "root-folders":
        rf_id = _int(parts[1], 0)
        if rf_id <= 0:
            raise WebError(400, "id inválido")
        if not mutate:
            folders = naming.list_root_folders(db)
            item = next((f for f in folders if f["id"] == rf_id), None)
            if not item:
                raise WebError(404, "carpeta no encontrada")
            return 200, item
        if body.get("_delete"):
            try:
                deleted = naming.delete_root_folder(db, rf_id)
            except ValueError as exc:
                raise WebError(400, str(exc))
            if not deleted:
                raise WebError(404, "carpeta no encontrada")
            return 200, {"deleted": True, "id": rf_id}
        try:
            saved = naming.save_root_folder(
                db,
                name=str(body.get("name") or "").strip(),
                kind=str(body.get("kind") or "any"),
                path=str(body.get("path") or "").strip(),
                is_default=bool(body.get("is_default")),
                folder_id=rf_id,
            )
        except ValueError as exc:
            raise WebError(400, str(exc))
        return 200, {"root_folder": saved}

    return None
