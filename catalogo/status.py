"""Estado de un título para la app: progreso, velocidad, buffer y fuente.

No arranca descargas ni crea intenciones. Consulta la base y, si hay cliente
de qBittorrent, los datos vivos del torrent para progreso/velocidad/ETA.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .db import connect
from .resolver import Resolution, Resolver
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


BUFFER_MIN_BYTES = 32 * 1024**2
BUFFER_MAX_BYTES = 128 * 1024**2
BUFFER_SECONDS = 60
PIECE_READY = 2


def _buffer_required(size: int | None) -> int:
    """Buffer de arranque estimado: ~60 s de vídeo, con topes.

    Para una película de 2 h: size/120. Un 1080p de 5,86 GB necesita ~48 MB;
    un 4K enorme se limita a 128 MB para no bloquear el arranque.
    """
    if not size:
        return BUFFER_MIN_BYTES
    return int(max(BUFFER_MIN_BYTES, min(BUFFER_MAX_BYTES, size / 120)))


def _buffer_state(
    qbit: StatusQbit | None,
    info_hash: str | None,
    files: list[dict],
    file_index: int | None,
    file_size: int | None,
) -> tuple[int | None, int, bool]:
    """Bytes contiguos listos desde el inicio del fichero y cola lista."""
    required = _buffer_required(file_size)
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
    tail_ready = (
        tail_piece < len(states) and states[tail_piece] == PIECE_READY
    )
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
        conn.commit()


def asset_status(
    db_path: str,
    key: str,
    *,
    release_id: int | None = None,
    qbit: StatusQbit | None = None,
    now: datetime | None = None,
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
            ORDER BY id DESC LIMIT 1
            """,
            (asset_id, release_id, release_id),
        ).fetchone()

        intent = conn.execute(
            "SELECT * FROM play_intents WHERE media_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (asset["key"],),
        ).fetchone()

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
    buffer_bytes, buffer_required, tail_ready = _buffer_state(
        qbit, info_hash, files, file_index, file_size
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
    runtime_seconds = int(asset["runtime_seconds"] or 0) or None
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
            int(release_row["id"]) if release_row is not None else None
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
        "downloaded_bytes": downloaded,
        "buffer_bytes": buffer_bytes,
        "buffer_required": buffer_required,
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
            commitment["seed_required_until"]
            if commitment is not None else None
        ),
        "updated_at": current.isoformat(),
    }
    return result
