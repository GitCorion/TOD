"""Estado de un título para la app: progreso, velocidad, buffer y fuente.

No arranca descargas ni crea intenciones. Consulta la base y, si hay cliente
de qBittorrent, los datos vivos del torrent para progreso/velocidad/ETA.
"""

from __future__ import annotations

import re
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .db import connect
from .policy import get_policy
from .resolver import Resolution, Resolver
from .retention import library_ready
from .stream import file_offset


class StatusQbit(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...
    def files(self, info_hash: str) -> list[dict]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ms_between(start: Any, end: Any) -> int | None:
    first = _parse_ts(start)
    last = _parse_ts(end)
    if first is None or last is None:
        return None
    return max(0, int((last - first).total_seconds() * 1000))


SWITCH_WINDOW_SECONDS = 120
INTENT_SESSION_GAP = timedelta(minutes=30)


def _current_intent(conn: Any, media_id: str, current: datetime) -> Any:
    """Primera intención de la sesión de reproducción actual.

    Jellyfin crea una intención por cada Range/seek. Para medir el arranque
    hay que usar la primera de la ráfaga, no la última, o el panel muestra
    el tiempo del último seek en vez del tiempo real de inicio.
    """
    rows = conn.execute(
        "SELECT * FROM play_intents WHERE media_id = ? AND created_at >= ? "
        "ORDER BY id DESC LIMIT 50",
        (media_id, (current - timedelta(hours=6)).isoformat()),
    ).fetchall()
    if not rows:
        return None
    session = rows[0]
    for older in rows[1:]:
        newest = _parse_ts(session["created_at"])
        previous = _parse_ts(older["created_at"])
        if newest is None or previous is None:
            break
        if (newest - previous) > INTENT_SESSION_GAP:
            break
        session = older
    return session


def _playback_state(
    *,
    stream_ready: bool,
    source: str,
    intent: Any,
    switched_at: Any,
    current: datetime,
    progress: float = 0.0,
) -> str:
    """Estado de arranque tal y como lo necesita el cliente.

    `ready` no depende de la intención: si hay fichero o buffer suficiente,
    se puede reproducir ya. `switching` gana a `preparing` porque el guard
    acaba de cambiar de fuente y el cliente debe reiniciar la petición.
    """
    if stream_ready:
        return "ready"
    if source in ("unavailable",):
        return "unavailable"
    switched = _parse_ts(switched_at)
    if (
        switched is not None
        and (current - switched).total_seconds() <= SWITCH_WINDOW_SECONDS
    ):
        return "switching"
    if intent is not None and intent["consumed_at"] is None:
        expires = _parse_ts(intent["expires_at"])
        if expires is None or expires > current:
            return "preparing"
    if source == "torrent" and progress > 0:
        return "buffering"
    return "idle"


def _live_torrent(
    qbit: StatusQbit | None, info_hash: str | None
) -> dict[str, Any] | None:
    if qbit is None or not info_hash:
        return None
    wanted = info_hash.lower()
    direct = getattr(qbit, "torrent", None)
    if callable(direct):
        try:
            found = direct(wanted)
        except Exception:
            found = None
        if found is not None:
            return found
    for torrent in qbit.torrents(category="ondemand"):
        if (torrent.get("hash") or "").lower() == wanted:
            return torrent
    # Un release puntual puede estar en otra categoría.
    for torrent in qbit.torrents():
        if (torrent.get("hash") or "").lower() == wanted:
            return torrent
    return None


def _state(
    resolution: Resolution | None,
    live: dict[str, Any] | None,
    progress: float,
) -> str:
    if live is not None:
        raw = (live.get("state") or "").lower()
        if "error" in raw or "missing" in raw:
            return "ERROR"
        if progress >= 1:
            return "SEEDING"
        if raw.startswith(("paused", "stopped")):
            return "PAUSED"
        if "dl" in raw or "download" in raw or "meta" in raw:
            return "DOWNLOADING"
        return raw.upper() or "UNKNOWN"
    if resolution is None:
        return "UNAVAILABLE"
    if resolution.source in ("library", "local_seed"):
        return "AVAILABLE"
    if resolution.source == "torrent":
        return "COLD"
    return "UNAVAILABLE"


def _eta(live: dict[str, Any] | None) -> int | None:
    if live is None:
        return None
    try:
        value = int(live.get("eta") or -1)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value >= 7 * 24 * 3600:
        return None
    return value


BUFFER_MIN_BYTES = 8 * 1024**2
BUFFER_MAX_BYTES = 64 * 1024**2
BUFFER_SECONDS = 10
PIECE_READY = 2
BUFFER_SAMPLE_WINDOW = 20.0
BUFFER_STALL_SECONDS = 5.0

# Últimas muestras de búfer por (info_hash, file_index). Se usa para saber
# si el prefijo contiguo está avanzando o clavado, y para estimar cuánto
# falta. Es una caché de proceso: status se consulta cada pocos segundos.
_BUFFER_SAMPLES: dict[tuple[str, int], list[tuple[float, int]]] = {}


def _buffer_required(
    size: int | None, runtime_seconds: int | None = None
) -> int:
    """Buffer de arranque: ~10 s de vídeo, con topes.

    Un 1080p de 11 Mbps necesita ~14 MB. Esperar 60 s de vídeo (lo que
    hacía antes) retrasaba el arranque sin necesidad: si el swarm va
    rápido, el colchón se llena solo. Sin duración conocida se estima
    12 s con size/240.
    """
    if not size:
        return BUFFER_MIN_BYTES
    if runtime_seconds and runtime_seconds > 0:
        wanted = (size / runtime_seconds) * BUFFER_SECONDS
    else:
        wanted = size / 240
    return int(max(BUFFER_MIN_BYTES, min(BUFFER_MAX_BYTES, wanted)))


def _buffer_dynamics(
    info_hash: str | None,
    file_index: int | None,
    buffer_bytes: int | None,
    buffer_required: int,
    *,
    now: float | None = None,
) -> tuple[bool, int | None]:
    """¿El búfer está clavado? ¿Cuánto falta para el objetivo?

    ``stalled`` es True si el prefijo contiguo no ha crecido en
    ``BUFFER_STALL_SECONDS``. ``eta`` solo se calcula con crecimiento
    positivo; un ETA del fichero mientras el búfer no avanza engaña.
    """
    if buffer_bytes is None or buffer_required <= 0:
        return False, None
    key = ((info_hash or "").lower(), int(file_index or 0))
    ts = time.monotonic() if now is None else now
    samples = _BUFFER_SAMPLES.setdefault(key, [])
    samples.append((ts, int(buffer_bytes)))
    cutoff = ts - BUFFER_SAMPLE_WINDOW
    while samples and samples[0][0] < cutoff:
        samples.pop(0)
    if len(_BUFFER_SAMPLES) > 256:
        for stale in [
            item for item, rows in _BUFFER_SAMPLES.items()
            if not rows or rows[-1][0] < ts - 120
        ]:
            _BUFFER_SAMPLES.pop(stale, None)
    if len(samples) < 2:
        return False, None
    span = max(0.001, samples[-1][0] - samples[0][0])
    growth = samples[-1][1] - samples[0][1]
    stalled = span >= BUFFER_STALL_SECONDS and growth <= 0
    if growth <= 0 or buffer_bytes >= buffer_required:
        return stalled, None
    eta = max(1, int((buffer_required - buffer_bytes) / (growth / span)))
    return stalled, eta


def _buffer_state(
    qbit: StatusQbit | None,
    info_hash: str | None,
    files: list[dict],
    file_index: int | None,
    file_size: int | None,
    runtime_seconds: int | None = None,
) -> tuple[int | None, int, bool]:
    """Bytes contiguos listos desde el inicio del fichero y cola lista."""
    required = _buffer_required(file_size, runtime_seconds)
    if (
        qbit is None
        or not info_hash
        or file_index is None
        or not file_size
    ):
        return None, required, False
    properties = getattr(qbit, "properties", None)
    pieces = getattr(qbit, "piece_states", None)
    if not callable(properties) or not callable(pieces):
        return None, required, False
    try:
        piece_size = int(
            (properties(info_hash) or {}).get("piece_size") or 0
        ) or 512 * 1024
        states = pieces(info_hash)
        offset = file_offset(files, file_index)
    except Exception:
        return None, required, False
    first_piece = max(0, offset // piece_size)
    total = 0
    for index in range(first_piece, len(states)):
        if states[index] != PIECE_READY:
            break
        total += piece_size
        if total >= file_size:
            total = file_size
            break
    tail_piece = max(0, (offset + file_size - 1) // piece_size)
    is_torrent_tail = tail_piece >= len(states) - 4
    piece_ready = (
        tail_piece < len(states) and states[tail_piece] == PIECE_READY
    )
    tail_ready = piece_ready if is_torrent_tail else True
    return min(total, file_size), required, tail_ready


def _mark_complete(
    db_path: str, torrent: Any, live: dict[str, Any]
) -> None:
    """Marca el torrent y sus ficheros listos en cuanto qBittorrent acaba.

    Evita que la app espere hasta el siguiente qbit-sync (5 min) viendo
    ``stream_ready: true`` pero con la resolución todavía en ``torrent``.
    """
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE torrents SET progress = 1, state = 'seeding',
                qbit_state = ?, completed_at = COALESCE(
                    completed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (live.get("state"), int(torrent["id"])),
        )
        rows = conn.execute(
            "SELECT * FROM torrent_files WHERE torrent_id = ?",
            (int(torrent["id"]),),
        ).fetchall()
        save_path = torrent["save_path"] or ""
        for row in rows:
            storage = (row["storage_location"] or "LOCAL_DISK").upper()
            rel_path = row["rel_path"] or ""
            path = (
                row["library_path"]
                or row["local_path"]
                or (str(Path(save_path) / rel_path) if rel_path else None)
            )
            if storage == "1FICHIER":
                conn.execute(
                    """
                    UPDATE torrent_files SET state = 'archived',
                        library_path = COALESCE(library_path, ?),
                        downloaded_bytes = size_bytes
                    WHERE id = ?
                    """,
                    (path, int(row["id"])),
                )
            else:
                conn.execute(
                    """
                    UPDATE torrent_files SET state = 'ready',
                        local_path = COALESCE(local_path, ?),
                        downloaded_bytes = size_bytes
                    WHERE id = ?
                    """,
                    (path, int(row["id"])),
                )
        rel_ids = {
            int(rid) for rid in ([torrent["release_id"]] + [r["release_id"] for r in rows])
            if rid is not None
        }
        for rid in rel_ids:
            conn.execute("DELETE FROM stream_failures WHERE release_id = ?", (rid,))
        conn.commit()



_DV_RE = re.compile(r"(^|[^a-z0-9])(dv|dovi)([^a-z0-9]|$)", re.IGNORECASE)
_4K_RE = re.compile(r"(^|[^a-z0-9])(4k|uhd|2160p?)([^a-z0-9]|$)", re.IGNORECASE)


def release_is_4k(release: Any) -> bool:
    quality = str((release["quality"] if release is not None else "") or "").strip()
    title = str((release["title"] if release is not None else "") or "").strip()
    return bool(_4K_RE.search(f"{quality} {title}"))


def release_is_dolby_vision(release: Any) -> bool:
    if release is None:
        return False
    keys = set(release.keys()) if hasattr(release, "keys") else set()
    hdr = str(release["hdr"] or "") if "hdr" in keys else ""
    flags = str(release["parsed_flags"] or "") if "parsed_flags" in keys else ""
    title = str(release["title"] or "") if "title" in keys else ""
    text = f"{hdr} {flags} {title}".lower()
    if "dolby vision" in text or "dolby.vision" in text or "dovi" in text:
        return True
    return bool(_DV_RE.search(text))


def is_release_compatible(
    release: Any,
    *,
    max_width: int | None = None,
    supports_4k: bool | None = None,
    supports_dv: bool | None = None,
) -> bool:
    if release is None:
        return True
    is_4k = release_is_4k(release)
    is_dv = release_is_dolby_vision(release)
    if (supports_4k is False or (max_width is not None and max_width < 3840)) and is_4k:
        return False
    if supports_dv is False and is_dv:
        return False
    return True


def _version_label(release: Any) -> str:
    q = str(release["quality"] or "unknown").lower()
    q_label = {"2160p": "4K", "uhd": "4K", "4k": "4K"}.get(q, q)
    a = str(release["audio"] or "unknown").lower()
    a_label = {
        "castellano": "Cast",
        "spanish_unverified": "ESP",
        "espanol": "ESP",
        "latino": "Lat",
        "vose": "VOSE",
        "unknown": "VO",
    }.get(a, a)
    return f"{q_label} {a_label}".strip()


def asset_versions(
    db_path: str,
    key: str,
    *,
    max_width: int | None = None,
    supports_4k: bool | None = None,
    supports_dv: bool | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    with closing(connect(db_path)) as conn:
        asset = conn.execute(
            "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
        if asset is None:
            return [], None
        asset_id = int(asset["id"])
        file_rows = conn.execute(
            """
            SELECT f.release_id, f.local_path, f.library_path, f.state,
                   COALESCE(NULLIF(t.info_hash, ''), r.info_hash) AS eff_hash,
                   r.*
            FROM torrent_files f
            LEFT JOIN torrents t ON t.id = f.torrent_id
            LEFT JOIN releases r ON r.id = f.release_id
            WHERE f.asset_id = ?
              AND f.state NOT IN ('evicted', 'missing')
            """,
            (asset_id,),
        ).fetchall()
        avail_ids: set[int] = set()
        avail_hashes: set[str] = set()
        avail_keys: set[tuple[str, str]] = set()
        pack_rels: list[Any] = []
        for fr in file_rows:
            if not (fr["local_path"] or fr["library_path"]):
                continue
            if fr["release_id"] is not None:
                avail_ids.add(int(fr["release_id"]))
            if fr["eff_hash"]:
                avail_hashes.add(str(fr["eff_hash"]).strip().lower())
            if fr["id"] is not None:
                avail_keys.add((str(fr["tracker"] or "").lower(), str(fr["title"] or "").strip().lower()))
                pack_rels.append(fr)

        rel_rows = conn.execute(
            """
            SELECT r.* FROM releases r
            WHERE r.asset_id = ?
              AND r.health <> 'blocked'
              AND NOT EXISTS (
                  SELECT 1 FROM tracker_policies tp
                  WHERE tp.tracker = r.tracker AND tp.enabled = 0
              )
            ORDER BY COALESCE(r.size_bytes, 999999999999) ASC, r.id DESC
            """,
            (asset_id,),
        ).fetchall()

    versions: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    merged_rels = list(rel_rows)
    seen_rel_ids = {int(r["id"]) for r in merged_rels}
    for pr in pack_rels:
        pr_id = int(pr["id"])
        pr_key = (str(pr["tracker"] or "").lower(), str(pr["title"] or "").strip().lower())
        if pr_id not in seen_rel_ids and not any(
            (str(r["tracker"] or "").lower(), str(r["title"] or "").strip().lower()) == pr_key
            for r in merged_rels
        ):
            merged_rels.append(pr)
            seen_rel_ids.add(pr_id)

    for rel in merged_rels:
        rid = int(rel["id"])
        ih = str(rel["info_hash"] or "").strip().lower()
        rel_key = (str(rel["tracker"] or "").lower(), str(rel["title"] or "").strip().lower())
        is_avail = rid in avail_ids or (bool(ih) and ih in avail_hashes) or (rel_key in avail_keys)
        is_alive = str(rel["health"] or "") == "alive" and int(rel["seeders"] or 0) >= 1
        if not is_avail and not is_alive:
            continue
        if ih and ih in seen_hashes:
            continue
        if ih:
            seen_hashes.add(ih)
        is_4k = release_is_4k(rel)
        is_dv = release_is_dolby_vision(rel)
        compat = is_release_compatible(
            rel,
            max_width=max_width,
            supports_4k=supports_4k,
            supports_dv=supports_dv,
        )
        q_low = str(rel["quality"] or "").lower()
        width, height = (3840, 2160) if is_4k else ((1280, 720) if "720" in q_low else (1920, 1080))
        versions.append({
            "release_id": rid,
            "label": _version_label(rel),
            "title": rel["title"],
            "quality": rel["quality"],
            "codec": rel["codec"],
            "audio": rel["audio"],
            "dolby_vision": is_dv,
            "width": width,
            "height": height,
            "size_bytes": rel["size_bytes"],
            "available": is_avail,
            "compatible": compat,
        })

    prefer_4k = supports_4k is True
    versions.sort(
        key=lambda v: (
            0 if (v["available"] and v["compatible"]) else (1 if v["available"] else (2 if v["compatible"] else 3)),
            (0 if int(v.get("width") or 0) >= 3840 else 1) if prefer_4k else (0 if "h264" in str(v.get("codec") or "").lower() or "avc" in str(v.get("codec") or "").lower() else 1),
            -int(v.get("size_bytes") or 0) if prefer_4k else int(v.get("size_bytes") or 999999999999),
            int(v["release_id"]),
        )
    )
    recommended = next(
        (v["release_id"] for v in versions if v["available"] and v["compatible"]),
        None,
    ) or next(
        (v["release_id"] for v in versions if v["compatible"]),
        None,
    )
    return versions, recommended


def asset_status(
    db_path: str,
    key: str,
    *,
    release_id: int | None = None,
    qbit: StatusQbit | None = None,
    now: datetime | None = None,
    max_width: int | None = None,
    supports_4k: bool | None = None,
    supports_dv: bool | None = None,
) -> dict[str, Any] | None:
    current = now or _now()
    with closing(connect(db_path)) as conn:
        asset = conn.execute(
            "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
        if asset is None:
            return None
        asset_id = int(asset["id"])

        params: list[Any] = [asset_id]
        release_filter = ""
        if release_id is not None:
            release_filter = "AND t.release_id = ?"
            params.append(int(release_id))
        torrent = conn.execute(
            f"""
            SELECT t.*, r.title AS release_title, r.quality, r.audio,
                   r.tracker AS release_tracker, r.privacy,
                   r.size_bytes AS release_size, r.health,
                   r.source AS release_source, r.id AS release_id
            FROM torrents t
            JOIN releases r ON r.id = t.release_id
            WHERE r.asset_id = ? {release_filter}
            ORDER BY t.id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if torrent is None:
            # Pack compartido: el torrent está enlazado al release del
            # episodio que lo arrancó. Se localiza por los ficheros del pack.
            torrent = conn.execute(
                """
                SELECT t.*, r.title AS release_title, r.quality, r.audio,
                       r.tracker AS release_tracker, r.privacy,
                       r.size_bytes AS release_size, r.health,
                       r.source AS release_source, r.id AS release_id
                FROM torrent_files f
                JOIN torrents t ON t.id = f.torrent_id
                JOIN releases r ON r.id = t.release_id
                WHERE f.asset_id = ? AND f.file_index IS NOT NULL
                ORDER BY f.id DESC
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
        file_row = conn.execute(
            """
            SELECT file_index FROM torrent_files
            WHERE asset_id = ? AND file_index IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (asset_id,),
        ).fetchone()

        if release_id is not None:
            release = conn.execute(
                "SELECT * FROM releases WHERE id = ? AND asset_id = ?",
                (int(release_id), asset_id),
            ).fetchone()
            if release is None:
                cand = conn.execute(
                    "SELECT * FROM releases WHERE id = ?",
                    (int(release_id),),
                ).fetchone()
                if cand is not None:
                    cand_hash = str(cand["info_hash"] or "").strip().lower()
                    linked = conn.execute(
                        """
                        SELECT 1 FROM torrent_files f
                        LEFT JOIN torrents t ON t.id = f.torrent_id
                        WHERE f.asset_id = ?
                          AND f.state NOT IN ('evicted', 'missing')
                          AND (f.release_id = ? OR (? != '' AND lower(COALESCE(t.info_hash, '')) = ?))
                        LIMIT 1
                        """,
                        (asset_id, int(release_id), cand_hash, cand_hash),
                    ).fetchone()
                    if linked is not None:
                        release = cand
            if release is None:
                return None
        elif torrent is not None:
            release = conn.execute(
                "SELECT * FROM releases WHERE id = ?",
                (int(torrent["release_id"]),),
            ).fetchone()
        else:
            release = None

        commitment = conn.execute(
            """
            SELECT * FROM seed_commitments
            WHERE asset_id = ?
              AND (? IS NULL OR release_id = ?)
            ORDER BY CASE state
                         WHEN 'seeding' THEN 0
                         WHEN 'active' THEN 1
                         WHEN 'approved' THEN 2
                         WHEN 'done' THEN 3
                         ELSE 4
                     END,
                     id DESC
            LIMIT 1
            """,
            (asset_id, release_id, release_id),
        ).fetchone()

        intent = _current_intent(conn, asset["key"], current)

    resolver = Resolver(db_path)
    resolution: Resolution | None = None
    try:
        if release_id is not None:
            resolution = resolver.resolve_release(key, int(release_id))
        elif release is not None and int(release["asset_id"]) == asset_id:
            resolution = resolver.resolve_release(
                key, int(release["id"])
            )
        else:
            # Pack compartido: el torrent pertenece a otro episodio. Se
            # resuelve por el fichero activo de ESTE episodio.
            resolution = resolver.resolve(key)
    except Exception:
        resolution = None

    info_hash = None
    if release is not None and release["info_hash"]:
        info_hash = release["info_hash"]
    if torrent is not None:
        info_hash = torrent["info_hash"] or info_hash
    if resolution is not None and resolution.info_hash:
        info_hash = resolution.info_hash
    live = _live_torrent(qbit, info_hash)

    progress = 0.0
    if live is not None:
        progress = float(live.get("progress") or 0)
    elif torrent is not None:
        progress = float(torrent["progress"] or 0)
    elif resolution is not None and resolution.source in (
        "library", "local_seed"
    ):
        progress = 1.0

    file_progress = None
    file_size = None
    files: list[dict] = []
    file_index = resolution.file_index if resolution is not None else None
    if file_index is None and file_row is not None:
        file_index = int(file_row["file_index"])
    if (
        live is not None
        and qbit is not None
        and info_hash
        and file_index is not None
    ):
        try:
            files = qbit.files(info_hash)
            for item in files:
                if int(item.get("index") or 0) == int(file_index):
                    file_progress = float(item.get("progress") or 0)
                    file_size = int(item.get("size") or 0) or None
                    break
        except Exception:
            file_progress = None
            file_size = None
            files = []
    if file_progress is not None:
        progress = file_progress
    runtime_seconds = int(asset["runtime_seconds"] or 0) or None
    buffer_bytes, buffer_required, tail_ready = _buffer_state(
        qbit, info_hash, files, file_index, file_size, runtime_seconds
    )
    buffer_stalled, buffer_eta_seconds = _buffer_dynamics(
        info_hash, file_index, buffer_bytes, buffer_required
    )

    size = None
    if file_size:
        size = file_size
    elif live is not None:
        size = int(live.get("size") or 0) or None
    if size is None and torrent is not None:
        size = int(torrent["release_size"] or 0) or None
    if size is None and resolution is not None:
        size = resolution.size
    if size is None and release is not None:
        size = int(release["size_bytes"] or 0) or None

    downloaded = None
    if file_progress is not None and file_size:
        downloaded = int(file_size * file_progress)
    elif live is not None:
        downloaded = int(live.get("downloaded") or 0)
    elif size is not None:
        downloaded = int(size * progress)

    speed = 0
    seeds = 0
    peers = 0
    swarm_seeds = 0
    swarm_peers = 0
    if live is not None:
        speed = int(live.get("dlspeed") or 0)
        seeds = int(live.get("num_seeds") or 0)
        peers = int(live.get("num_leechs") or 0)
        swarm_seeds = int(live.get("num_complete") or 0)
        swarm_peers = int(live.get("num_incomplete") or 0)

    # Al llegar al 100 %, pasar de torrent a local_seed sin esperar al
    # worker de 5 minutos. Así la app puede arrancar en el siguiente poll.
    if (
        live is not None
        and resolution is not None
        and resolution.source == "torrent"
        and float(live.get("progress") or 0) >= 0.999
    ):
        if torrent is None and resolution.info_hash:
            with closing(connect(db_path)) as conn:
                torrent = conn.execute(
                    "SELECT * FROM torrents WHERE info_hash = ?",
                    (resolution.info_hash.lower(),),
                ).fetchone()
        if torrent is not None:
            try:
                _mark_complete(db_path, torrent, live)
                if release_id is None:
                    refreshed = resolver.resolve(key)
                else:
                    refreshed = resolver.resolve_release(key, int(release_id))
                if refreshed is not None:
                    resolution = refreshed
            except Exception:
                pass

    source = resolution.source if resolution is not None else "unavailable"
    if source == "torrent":
        # Requiere el buffer contiguo inicial y que la cola esté lista si
        # el fichero coincide con el final del torrent (priorizado por first_last).
        buffered = (
            buffer_bytes is not None
            and buffer_required > 0
            and buffer_bytes >= buffer_required
            and tail_ready
        )
        stream_ready = bool(
            buffered or (progress >= 1 and live is not None)
        )
    else:
        stream_ready = source in ("library", "local_seed")
    state = _state(resolution, live, progress)
    pack_progress = None
    if live is not None:
        pack_progress = round(float(live.get("progress") or 0), 4)
    if stream_ready:
        buffer_stalled = False
    waiting_reason = None
    if not bool(stream_ready):
        if source in ("unavailable",):
            waiting_reason = "unavailable"
        elif source in ("torrent", "arr"):
            if buffer_bytes is None:
                waiting_reason = "pieces"
            else:
                waiting_reason = "buffer"
        else:
            waiting_reason = source
    playback_state = _playback_state(
        stream_ready=bool(stream_ready),
        source=source,
        intent=intent,
        switched_at=asset["stream_switched_at"],
        current=current,
        progress=float(progress or 0),
    )
    prepare_ms = (
        _ms_between(intent["created_at"], intent["ready_at"])
        if intent is not None
        else None
    )
    first_byte_ms = (
        _ms_between(intent["created_at"], intent["first_byte_at"])
        if intent is not None
        else None
    )

    seed_policy = None
    if commitment is not None:
        with closing(connect(db_path)) as conn:
            seed_policy = get_policy(conn, commitment["tracker"])
    seed_active = commitment is not None and commitment["state"] in (
        "approved", "active", "seeding"
    )
    seed_started = (
        _parse_ts(commitment["seed_started_at"])
        if commitment is not None else None
    ) or (
        _parse_ts(torrent["seed_started_at"]) if torrent is not None else None
    ) or (
        _parse_ts(torrent["completed_at"]) if torrent is not None else None
    )
    seed_deadline = (
        _parse_ts(commitment["seed_required_until"])
        if seed_active else None
    )
    if (
        seed_active
        and seed_deadline is None
        and seed_started is not None
        and seed_policy is not None
    ):
        seed_deadline = seed_started + timedelta(
            hours=max(0, int(seed_policy.min_seed_hours))
        )
    seed_ratio = None
    if commitment is not None and commitment["info_hash"]:
        with closing(connect(db_path)) as conn:
            seed_torrent = conn.execute(
                "SELECT ratio FROM torrents WHERE info_hash = ? "
                "ORDER BY id DESC LIMIT 1",
                (commitment["info_hash"],),
            ).fetchone()
        if seed_torrent is not None:
            seed_ratio = float(seed_torrent["ratio"] or 0)
    if seed_ratio is None:
        if live is not None:
            seed_ratio = float(live.get("ratio") or 0)
        elif torrent is not None:
            seed_ratio = float(torrent["ratio"] or 0)
    seed_target = None
    if commitment is not None:
        seed_target = float(
            commitment["target_ratio"]
            or (seed_policy.target_ratio if seed_policy else 0)
            or 0
        )
    seed = None
    if commitment is not None:
        seed = {
            "state": commitment["state"],
            "started_at": seed_started.isoformat() if seed_started else None,
            "required_until": (
                seed_deadline.isoformat() if seed_deadline else None
            ),
            "remaining_seconds": (
                max(0, int((seed_deadline - current).total_seconds()))
                if seed_deadline is not None else None
            ),
            "ratio": round(seed_ratio or 0, 3),
            "target_ratio": seed_target,
            "tracker_verdict": commitment["tracker_verdict"],
            "tracker_message": commitment["tracker_message"],
            "tracker_checked_at": commitment["tracker_checked_at"],
            "library_ready": (
                library_ready(asset_id, db_path=db_path) if seed_active else False
            ),
        }
    eta_seconds = _eta(live)
    if file_progress is not None and file_size and speed > 0:
        if file_progress >= 1:
            eta_seconds = None
        else:
            eta_seconds = max(
                1, int(file_size * (1 - file_progress) / speed)
            )

    release_row = release if release is not None else None
    # En packs, el torrent pertenece a un episodio, pero el fichero activo
    # puede ser otro. Se devuelve el release del episodio si es válido para
    # este asset; así la app pide Prepare con el release_id correcto.
    if resolution is not None and resolution.release_id is not None:
        with closing(connect(db_path)) as conn:
            candidate = conn.execute(
                "SELECT * FROM releases WHERE id = ? AND asset_id = ?",
                (int(resolution.release_id), asset_id),
            ).fetchone()
        if candidate is not None:
            release_row = candidate
        elif release_row is None:
            with closing(connect(db_path)) as conn:
                release_row = conn.execute(
                    "SELECT * FROM releases WHERE id = ?",
                    (int(resolution.release_id),),
                ).fetchone()
    swarm_row = None
    if release_row is not None:
        with closing(connect(db_path)) as conn:
            swarm_row = conn.execute(
                "SELECT * FROM swarm_stats WHERE release_id = ?",
                (int(release_row["id"]),),
            ).fetchone()
    runtime_seconds = runtime_seconds or int(asset["runtime_seconds"] or 0) or None
    required_speed_bps = (
        int(size / runtime_seconds)
        if size and runtime_seconds
        else None
    )
    result: dict[str, Any] = {
        "key": asset["key"],
        "asset_id": asset_id,
        "title": asset["title"],
        "year": asset["year"],
        "stream_generation": int(asset["stream_generation"] or 0),
        "stream_switched_at": asset["stream_switched_at"],
        "stream_switch_reason": asset["stream_switch_reason"],
        "switch_count": int(asset["stream_generation"] or 0),
        "prepare_ms": prepare_ms,
        "first_byte_ms": first_byte_ms,
        "intent_id": int(intent["id"]) if intent is not None else None,
        "intent_source": intent["source"] if intent is not None else None,
        "state": state,
        "playback_state": playback_state,
        "source": source,
        "reason": resolution.reason if resolution is not None else None,
        "stream_ready": bool(stream_ready),
        "release_id": (
            int(release_row["id"])
            if release_row is not None
            and release_row["health"] not in ("dead", "stale")
            else None
        ),
        "quality": release_row["quality"] if release_row is not None else None,
        "audio": release_row["audio"] if release_row is not None else None,
        "tracker": (
            release_row["tracker"] if release_row is not None else None
        ),
        "privacy": (
            release_row["privacy"] if release_row is not None else None
        ),
        "info_hash": info_hash,
        "progress": round(progress, 4),
        "pack_progress": pack_progress,
        "is_pack": (
            bool(release_row["is_pack"]) if release_row is not None else None
        ),
        "files_count": len(files) if files else None,
        "waiting_reason": waiting_reason,
        "downloaded_bytes": downloaded,
        "buffer_bytes": buffer_bytes,
        "buffer_required": buffer_required,
        "buffer_stalled": bool(buffer_stalled),
        "buffer_eta_seconds": buffer_eta_seconds,
        "tail_ready": tail_ready,
        "runtime_seconds": runtime_seconds,
        "required_speed_bps": required_speed_bps,
        "swarm_connected_seeds": (
            int(swarm_row["connected_seeds"]) if swarm_row else None
        ),
        "swarm_speed_bps": (
            int(swarm_row["speed_bps"]) if swarm_row else None
        ),
        "swarm_started_ok": (
            bool(swarm_row["started_ok"]) if swarm_row else None
        ),
        "swarm_failures": int(swarm_row["failures"]) if swarm_row else None,
        "swarm_last_seen": swarm_row["last_seen"] if swarm_row else None,
        "size_bytes": size,
        "speed_bps": speed,
        "eta_seconds": eta_seconds,
        "seeds": seeds,
        "peers": peers,
        "swarm_seeds": swarm_seeds,
        "swarm_peers": swarm_peers,
        "has_local": source in ("library", "local_seed"),
        "file_id": resolution.file_id if resolution is not None else None,
        "file_index": file_index,
        "seed_state": commitment["state"] if commitment is not None else None,
        "seed_required_until": (
            seed_deadline.isoformat() if seed_deadline is not None else None
        ),
        "seed": seed,
        "updated_at": current.isoformat(),
    }
    versions, recommended_release_id = asset_versions(
        db_path,
        key,
        max_width=max_width,
        supports_4k=supports_4k,
        supports_dv=supports_dv,
    )
    result["versions"] = versions
    result["recommended_release_id"] = recommended_release_id
    return result
