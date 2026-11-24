"""Puesta a punto puntual del seguimiento de todos los títulos.

Marca todo como monitoreado para una pasada de búsqueda de fuentes. Es una
operación de mantenimiento de una vez: el espejo de Radarr/Sonarr vuelve a
gestionar después el estado de lo que ya está en los *arr.
"""

from __future__ import annotations

from contextlib import closing

from .db import connect


def monitor_all(
    db_path,
    *,
    dry_run: bool = True,
    include_arr: bool = True,
    source: str = "bulk",
) -> dict:
    """Activa el seguimiento de todas las fichas, una sola vez.

    El origen se fuerza a ``bulk`` (no se usa COALESCE) para que el espejo
    de los *arr no vuelva a desmarcarlas: su consulta que desactiva el
    seguimiento solo mira los orígenes automáticos.
    """
    pending = "monitored = 0"
    target = "1 = 1"
    params: list[object] = []
    if not include_arr:
        pending += " AND library_added = 0"
        target = "library_added = 0"
    with closing(connect(db_path)) as conn:
        counts = {
            row["kind"]: int(row["n"])
            for row in conn.execute(
                f"SELECT kind, COUNT(*) AS n FROM media_items WHERE {pending} GROUP BY kind",
                params,
            )
        }
        changed = int(counts.get("movie", 0) + counts.get("series", 0))
        if not dry_run:
            conn.execute(
                f"""
                UPDATE media_items SET
                    monitored = 1,
                    monitored_at = COALESCE(
                        monitored_at,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    ),
                    monitor_source = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE {target}
                """,
                [source, *params],
            )
            conn.commit()
    return {
        "changed": changed,
        "movies": counts.get("movie", 0),
        "series": counts.get("series", 0),
        "include_arr": bool(include_arr),
        "dry_run": dry_run,
    }
