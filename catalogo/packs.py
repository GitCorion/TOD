"""Poda de packs de serie asignados a una temporada que no les toca.

La captura antigua aceptaba cualquier release sin ``SxxEyy`` como pack, sin
comprobar su temporada. Este mantenimiento elimina esas filas y corrige la
marca ``is_pack`` de episodios que se colaron como packs.

Nunca borra un release con torrent, ficheros o compromisos asociados: esos
se listan como referenciados para revisarlos a mano.
"""

from __future__ import annotations

from contextlib import closing
from typing import Iterable

from .db import connect
from .release_parse import parse_release

_REFERENCE_TABLES = (
    "torrents",
    "torrent_files",
    "play_targets",
    "seed_commitments",
    "stream_failures",
    "swarm_stats",
)


def _chunks(values: list[int], size: int = 500) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _referenced_release_ids(conn) -> set[int]:
    referenced: set[int] = set()
    for table in _REFERENCE_TABLES:
        for row in conn.execute(
            f"SELECT DISTINCT release_id FROM {table} "
            "WHERE release_id IS NOT NULL"
        ):
            referenced.add(int(row[0]))
    return referenced


def prune_misassigned_packs(db_path, *, dry_run: bool = True) -> dict:
    """Elimina packs de otra temporada y corrige flags de episodios."""
    report: dict = {
        "checked": 0,
        "delete": 0,
        "flag_fixed": 0,
        "referenced": [],
        "samples": [],
        "dry_run": dry_run,
    }
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.asset_id, r.title, r.is_pack, m.key, m.season
            FROM releases r
            JOIN media_items m ON m.id = r.asset_id
            WHERE m.kind = 'series'
            """
        ).fetchall()
        referenced = _referenced_release_ids(conn)
        delete_ids: list[int] = []
        flag_ids: list[int] = []
        for row in rows:
            report["checked"] += 1
            parsed = parse_release(row["title"])
            if parsed.episodes:
                if int(row["is_pack"]):
                    flag_ids.append(int(row["id"]))
                continue
            if not int(row["is_pack"]):
                continue
            if parsed.complete_series or parsed.season is None:
                continue
            if parsed.season == int(row["season"] or 0):
                continue
            item = {
                "id": int(row["id"]),
                "key": row["key"],
                "asset_season": int(row["season"] or 0),
                "pack_season": int(parsed.season),
                "title": row["title"],
            }
            if int(row["id"]) in referenced:
                report["referenced"].append(item)
                continue
            delete_ids.append(int(row["id"]))
            if len(report["samples"]) < 10:
                report["samples"].append(item)

        report["delete"] = len(delete_ids)
        report["flag_fixed"] = len(flag_ids)
        if not dry_run:
            for chunk in _chunks(delete_ids):
                conn.execute(
                    "DELETE FROM releases WHERE id IN ("
                    + ",".join("?" for _ in chunk)
                    + ")",
                    chunk,
                )
            for chunk in _chunks(flag_ids):
                conn.execute(
                    "UPDATE releases SET is_pack = 0 WHERE id IN ("
                    + ",".join("?" for _ in chunk)
                    + ")",
                    chunk,
                )
            conn.commit()
    return report
