"""Diagnóstico de solo lectura del catálogo.

Comprueba montajes, estado de qBittorrent y coherencia de la base sin
cambiar nada. Sirve para detectar situaciones como un remontaje que deja
cientos de torrents verificando o compromisos activos sin torrent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .db import connect

DEFAULT_PATHS = ("/mnt/descargas", "/mnt/library", "/mnt/cache")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mounts(paths: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in paths:
        item: dict[str, Any] = {"exists": os.path.isdir(path)}
        if item["exists"]:
            item["writable"] = os.access(path, os.W_OK)
            try:
                usage = shutil.disk_usage(path)
                item["free_bytes"] = usage.free
                item["total_bytes"] = usage.total
            except OSError as exc:
                item["error"] = str(exc)
        result[path] = item
    return result


def _qbit_state(client: Any) -> dict[str, Any]:
    torrents = client.torrents()
    states = Counter(
        (item.get("state") or "").lower() for item in torrents
    )
    categories = Counter(item.get("category") or "" for item in torrents)
    ondemand = Counter(
        (item.get("state") or "").lower()
        for item in torrents
        if (item.get("category") or "") == "ondemand"
    )
    checking = sum(
        count for state, count in states.items()
        if state.startswith("checking")
    )
    queued = states.get("queueddl", 0) + states.get("queuedup", 0)
    prefs_fn = getattr(client, "preferences", None)
    private_safety = None
    if callable(prefs_fn):
        try:
            prefs = prefs_fn() or {}
            if isinstance(prefs, dict) and prefs:
                private_safety = {
                    "dht": bool(prefs.get("dht")),
                    "pex": bool(prefs.get("pex")),
                    "lsd": bool(prefs.get("lsd")),
                    "anonymous_mode": bool(prefs.get("anonymous_mode")),
                    "listen_port": prefs.get("listen_port"),
                    "incomplete_files_ext": bool(prefs.get("incomplete_files_ext")),
                }
        except Exception:
            private_safety = None
    return {
        "version": client.app_version(),
        "torrents": len(torrents),
        "states": dict(states.most_common()),
        "categories": dict(categories.most_common()),
        "ondemand_states": dict(ondemand.most_common()),
        "checking": checking,
        "queued": queued,
        "private_safety": private_safety,
    }


def run_doctor(
    db_path,
    *,
    qbit: Any | None = None,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    report: dict[str, Any] = {
        "time": current.isoformat(),
        "mounts": _mounts(paths),
        "qbit": None,
        "db": {},
        "warnings": [],
    }
    if qbit is not None:
        try:
            report["qbit"] = _qbit_state(qbit)
        except Exception as exc:
            report["warnings"].append(f"qBittorrent: {str(exc)[:160]}")
    with closing(connect(db_path)) as conn:
        tables = (
            "media_items", "releases", "torrents", "torrent_files",
            "seed_commitments", "play_targets", "stream_failures",
            "stream_leases", "library_files",
        )
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        commitments = {
            row["state"]: row["n"]
            for row in conn.execute(
                "SELECT state, COUNT(*) n FROM seed_commitments GROUP BY state"
            )
        }
        active_failures = conn.execute(
            """
            SELECT COUNT(*) FROM stream_failures
            WHERE until > ?
            """,
            (current.isoformat(),),
        ).fetchone()[0]
        recent_leases = conn.execute(
            """
            SELECT COUNT(*) FROM stream_leases
            WHERE last_seen >= ?
            """,
            ((current - timedelta(minutes=5)).isoformat(),),
        ).fetchone()[0]
        orphans = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.id, c.asset_id, c.release_id, c.tracker, c.state,
                       a.key
                FROM seed_commitments c
                JOIN media_items a ON a.id = c.asset_id
                WHERE c.state IN ('approved', 'active')
                  AND NOT EXISTS (
                      SELECT 1 FROM torrents t
                      WHERE t.release_id = c.release_id
                         OR (
                            c.info_hash IS NOT NULL
                            AND c.info_hash <> ''
                            AND t.info_hash = c.info_hash
                         )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM torrent_files f
                      JOIN torrents t ON t.id = f.torrent_id
                      WHERE (
                          f.release_id = c.release_id
                          OR f.asset_id = c.asset_id
                      )
                        AND t.state NOT IN ('removed', 'error')
                  )
                ORDER BY c.id DESC
                LIMIT 20
                """
            ).fetchall()
        ]
        report["db"] = {
            "counts": counts,
            "commitments_by_state": commitments,
            "active_failures": int(active_failures),
            "recent_leases": int(recent_leases),
            "orphan_commitments": len(orphans),
            "orphans": orphans,
        }
    for path, item in report["mounts"].items():
        if not item.get("exists"):
            report["warnings"].append(f"montaje ausente: {path}")
        elif not item.get("writable"):
            report["warnings"].append(f"montaje sin escritura: {path}")
    q = report["qbit"]
    if q:
        if q["checking"] >= 50:
            report["warnings"].append(
                f"qBittorrent verificando {q['checking']} torrents; "
                "posible remontaje o recuperación"
            )
        if q["queued"] >= 200:
            report["warnings"].append(
                f"cola de descargas grande: {q['queued']} en cola"
            )
        ps = q.get("private_safety") or {}
        if ps:
            if ps.get("dht") or ps.get("pex") or ps.get("lsd"):
                report["warnings"].append(
                    "qBittorrent tiene DHT/PeX/LSD activo; desactívalo para trackers privados"
                )
            if ps.get("anonymous_mode"):
                report["warnings"].append(
                    "qBittorrent tiene anonymous_mode activo; puede perjudicar announces en trackers privados"
                )
            if ps.get("listen_port") == 6881:
                report["warnings"].append(
                    "qBittorrent usa el puerto 6881; muchos trackers privados lo bloquean"
                )
            if ps.get("incomplete_files_ext"):
                report["warnings"].append(
                    "qBittorrent añade extensión .!qB; desactívalo para lectura directa de streams"
                )
    db = report["db"]
    if db["orphan_commitments"]:
        report["warnings"].append(
            f"{db['orphan_commitments']} compromisos activos sin torrent"
        )
    if db["active_failures"]:
        report["warnings"].append(
            f"{db['active_failures']} releases descartados temporalmente"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalogo-doctor")
    parser.add_argument("--db")
    parser.add_argument("--no-qbit", action="store_true")
    parser.add_argument("--path", action="append", default=[])
    args = parser.parse_args(argv)
    config = Config.from_env()
    db_path = args.db or str(config.db_path)
    qbit = None
    if not args.no_qbit and config.qbittorrent_pass:
        from .qbittorrent import QbittorrentClient

        try:
            qbit = QbittorrentClient.from_config(config)
        except Exception:
            qbit = None
    paths = tuple(args.path) or DEFAULT_PATHS
    report = run_doctor(db_path, qbit=qbit, paths=paths)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
