"""Quita el force-start a los torrents completos.

Durante la descarga se usa `forceStart` para que el episodio pedido no se
quede en cola. Al llegar al 100 % hay que soltarlo: si no, qBittorrent lo
mantiene como `forcedUP` y no aplica las reglas normales de siembra.

Este módulo solo toca torrents del catálogo (release vinculado o categoría
`ondemand`) que ya están completos.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Protocol

from .db import connect

ONDEMAND_CATEGORY = "ondemand"
FORCED_STATES = ("forcedup", "forceddl")


class SeedingClient(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...
    def set_force_start(self, info_hash: str, value: bool = True) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _managed_hashes(db_path: str) -> set[str]:
    """Torrents del catálogo: con release vinculado o de la categoría ondemand."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT info_hash FROM torrents "
            "WHERE release_id IS NOT NULL OR category = ?",
            (ONDEMAND_CATEGORY,),
        ).fetchall()
    return {(row["info_hash"] or "").lower() for row in rows if row["info_hash"]}


def _db_torrent_id(db_path: str, info_hash: str) -> int | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id FROM torrents WHERE info_hash = ?", (info_hash,)
        ).fetchone()
    return int(row["id"]) if row else None


def normalize_completed(
    db_path: str,
    client: SeedingClient,
    *,
    limit: int = 50,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    managed = _managed_hashes(db_path)
    report: dict[str, Any] = {
        "checked": 0,
        "planned": [],
        "normalized": [],
        "skipped_incomplete": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    for torrent in client.torrents():
        info_hash = (torrent.get("hash") or "").lower()
        if not info_hash or info_hash not in managed:
            continue
        state = (torrent.get("state") or "").lower()
        if state not in FORCED_STATES:
            continue
        report["checked"] += 1
        if float(torrent.get("progress") or 0) < 1:
            report["skipped_incomplete"] += 1
            continue
        item = {
            "info_hash": info_hash,
            "name": torrent.get("name"),
            "qbit_state": torrent.get("state"),
        }
        if dry_run:
            report["planned"].append(item)
            if len(report["planned"]) >= limit:
                break
            continue
        try:
            client.set_force_start(info_hash, False)
        except Exception as exc:
            report["errors"].append(f"{info_hash[:12]}: {str(exc)[:200]}")
            continue
        torrent_id = _db_torrent_id(db_path, info_hash)
        if torrent_id is not None:
            with closing(connect(db_path)) as conn:
                # El siguiente qbit-sync pondrá el estado real (uploading,
                # stalledUP, queuedUP...). Evitamos seguir marcándolo forzado.
                conn.execute(
                    "UPDATE torrents SET qbit_state = 'normalizing', "
                    "updated_at = ? WHERE id = ?",
                    (current.isoformat(), torrent_id),
                )
                conn.commit()
        report["normalized"].append(item)
        if len(report["normalized"]) >= limit:
            break
    return report
