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

        # Un tracker puede tener releases sin fila de política (instalaciones
        # antiguas o trackers descubiertos después). Sin fila, el bloqueo no
        # se vería en Configuración. Se crea con los valores por defecto.
        from .policy import ensure_default_policy

        privacy = conn.execute(
            "SELECT privacy FROM releases "
            "WHERE tracker = ? AND privacy IS NOT NULL AND privacy <> '' "
            "LIMIT 1",
            (tracker,),
        ).fetchone()
        ensure_default_policy(
            conn, tracker, privacy[0] if privacy is not None else None
        )
        report["policy"] = "disabled" if blocked else "enabled"

        # La política se cambia antes que los releases: el trigger de
        # bloqueo mira `enabled`, y al desbloquear hay que apagarlo primero.
        if blocked:
            conn.execute(
                """
                UPDATE tracker_policies
                SET enabled = 0,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ?
                """,
                (tracker,),
            )
            conn.execute(
                """
                UPDATE releases
                SET health = 'blocked', health_checked_at = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ? AND health <> 'blocked'
                """,
                (current.isoformat(), tracker),
            )
        else:
            conn.execute(
                """
                UPDATE tracker_policies
                SET enabled = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ?
                """,
                (tracker,),
            )
            conn.execute(
                """
                UPDATE releases
                SET health = 'unknown', health_checked_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE tracker = ? AND health = 'blocked'
                """,
                (tracker,),
            )
        conn.commit()
        report["reason"] = reason
    return report


def delete_tracker_strm(
    db_path,
    out_dir,
    tracker: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Borra los `.strm` que dependen de los releases de un tracker.

    - Las versiones (`&release=<id>`) del tracker se eliminan siempre.
    - El `.strm` principal solo se elimina si el título se queda sin otra
      fuente: otro release vivo, un fichero local/archivado o un fichero de
      Radarr/Sonarr. Si queda algo reproducible, el principal se conserva.
    """
    import urllib.parse
    from pathlib import Path

    root = Path(out_dir)
    report = {
        "tracker": tracker,
        "variants": 0,
        "main": 0,
        "kept": 0,
        "errors": 0,
        "total": 0,
        "dry_run": dry_run,
    }
    if not root.is_dir():
        return report

    with closing(connect(db_path)) as conn:
        release_ids = {
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM releases WHERE tracker = ?", (tracker,)
            ).fetchall()
        }
        if not release_ids:
            return report
        assets = {
            row["key"]: dict(row)
            for row in conn.execute(
                "SELECT id, key, kind, tmdb, season, episode FROM media_items"
            ).fetchall()
        }

        def has_other_source(asset: dict) -> bool:
            asset_id = int(asset["id"])
            if conn.execute(
                """
                SELECT 1 FROM releases
                WHERE asset_id = ? AND tracker <> ? AND (
                    (health = 'alive' AND COALESCE(seeders, 0) >= 1)
                    OR (debrid_cached = 1 AND COALESCE(debrid_link, '') <> '')
                )
                LIMIT 1
                """,
                (asset_id, tracker),
            ).fetchone():
                return True
            if conn.execute(
                """
                SELECT 1 FROM torrent_files
                WHERE asset_id = ?
                  AND state IN ('ready', 'archived', 'seeding', 'complete')
                  AND (local_path IS NOT NULL OR library_path IS NOT NULL)
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone():
                return True
            return bool(
                conn.execute(
                    """
                    SELECT 1 FROM library_files
                    WHERE kind = ? AND tmdb = ?
                      AND (
                          ? = 'movie'
                          OR (season = ? AND episode = ?)
                      )
                    LIMIT 1
                    """,
                    (
                        asset["kind"],
                        int(asset["tmdb"] or 0),
                        asset["kind"],
                        int(asset["season"] or 0),
                        int(asset["episode"] or 0),
                    ),
                ).fetchone()
            )

        deleted_dirs: set[Path] = set()
        for base in ("movies", "series"):
            directory = root / base
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.strm")):
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore").strip()
                except OSError:
                    continue
                try:
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlparse(content).query
                    )
                except Exception:
                    continue
                release = (query.get("release") or [None])[0]
                if release is not None:
                    try:
                        if int(release) in release_ids:
                            report["variants"] += 1
                            if not dry_run:
                                try:
                                    path.unlink()
                                    deleted_dirs.add(path.parent)
                                except OSError:
                                    report["errors"] += 1
                    except (TypeError, ValueError):
                        pass
                    continue

                key = (query.get("key") or [None])[0]
                asset = assets.get(key) if key else None
                if not asset:
                    continue
                if has_other_source(asset):
                    report["kept"] += 1
                    continue
                report["main"] += 1
                if not dry_run:
                    try:
                        path.unlink()
                        deleted_dirs.add(path.parent)
                    except OSError:
                        report["errors"] += 1

        if not dry_run:
            stop = {root / "movies", root / "series"}
            for directory in sorted(
                deleted_dirs, key=lambda item: len(item.parts), reverse=True
            ):
                parent = directory
                while (
                    parent not in stop
                    and parent != root
                    and parent.is_dir()
                    and not any(parent.iterdir())
                ):
                    parent.rmdir()
                    parent = parent.parent

    report["total"] = report["variants"] + report["main"]
    if not dry_run:
        from . import strm_inventory

        strm_inventory.invalidate(root)
    return report
