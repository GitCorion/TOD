"""Streaming HTTP desde un fichero parcial de qBittorrent.

- Comprueba piezas globales del torrent (incluye offset de packs).
- Seek corto: espera piezas hasta timeout; seek lejano: 503 antes de headers.
- Busca el fichero en temp, save y archivo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import media_type

CHUNK = 1024 * 1024
BUFFER = 4 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
STREAM_CHUNK_TIMEOUT = 60.0
PIECE_READY = 2
# Margen para no rechazar un seek que está a punto de llegar.
SEEK_TOLERANCE = 8 * 1024 * 1024

STREAM_ROOTS = [
    "/mnt/cache/tmp/ondemand",
    "/mnt/cache/tmp",
    "/mnt/descargas/ondemand",
    "/mnt/descargas/catalogo",
    "/mnt/descargas/radarr",
    "/mnt/descargas/tv-sonarr",
    "/mnt/library/archive",
]


class StreamTimeout(RuntimeError):
    pass


class SeekNotReady(StreamTimeout):
    """El rango pedido queda lejos del frente de descarga.

    qBittorrent descarga en orden y no permite priorizar una pieza suelta:
    bloquear la petición solo alarga la espera del reproductor. Se informa
    de la posición real para que el cliente reintente más tarde.
    """

    def __init__(self, position: int, available: int):
        super().__init__(
            f"seek pendiente: posición {position}, disponible {available}"
        )
        self.position = int(position)
        self.available = int(available)


class RangeNotSatisfiable(RuntimeError):
    pass


class QbitRead(Protocol):
    def files(self, info_hash: str) -> list[dict]: ...
    def properties(self, info_hash: str) -> dict: ...
    def piece_states(self, info_hash: str) -> list[int]: ...


def file_entry(files: list[dict], file_index: int) -> dict:
    for item in files:
        if int(item.get("index") or 0) == int(file_index):
            return item
    raise StreamTimeout(f"file_index {file_index} no existe en el torrent")


def file_offset(files: list[dict], file_index: int) -> int:
    ordered = sorted(files, key=lambda item: int(item.get("index") or 0))
    offset = 0
    for item in ordered:
        if int(item.get("index") or 0) == int(file_index):
            return offset
        offset += int(item.get("size") or 0)
    raise StreamTimeout(f"file_index {file_index} no existe")


def find_file(rel_path: str, roots: list[str] | None = None) -> Path | None:
    normalized = rel_path.replace("\\", "/")
    for root in roots or STREAM_ROOTS:
        candidate = Path(root) / normalized
        if candidate.is_file():
            return candidate
    return None


def wait_for_file(
    rel_path: str,
    roots: list[str] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        path = find_file(rel_path, roots)
        if path is not None:
            return path
        time.sleep(0.5)
    raise StreamTimeout(f"fichero no creado: {rel_path}")


def wait_pieces(
    qbit: QbitRead,
    info_hash: str,
    first_byte: int,
    length: int,
    piece_size: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    if piece_size <= 0:
        piece_size = 512 * 1024
    start_piece = max(0, int(first_byte) // piece_size)
    end_piece = max(
        start_piece,
        (int(first_byte) + max(1, int(length)) - 1) // piece_size,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        states = qbit.piece_states(info_hash)
        if len(states) > end_piece and all(
            states[piece] == PIECE_READY
            for piece in range(start_piece, end_piece + 1)
        ):
            return True
        time.sleep(0.5)
    return False


def stream_torrent_file(
    handler: Any,
    qbit: QbitRead,
    info_hash: str,
    file_index: int,
    start: int,
    end: int | None,
    *,
    roots: list[str] | None = None,
    head: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    buffer_bytes: int = BUFFER,
    on_first_byte: Callable[[], None] | None = None,
) -> None:
    files = qbit.files(info_hash)
    item = file_entry(files, file_index)
    size = int(item.get("size") or 0)
    if size <= 0:
        raise StreamTimeout("fichero sin tamaño")
    if start >= size:
        raise RangeNotSatisfiable("range fuera de tamaño")
    end = size - 1 if end is None else min(int(end), size - 1)
    if end < start:
        raise RangeNotSatisfiable("range inválido")
    length = end - start + 1

    props = qbit.properties(info_hash)
    piece_size = int(props.get("piece_size") or 0) or 512 * 1024
    offset = file_offset(files, file_index)
    rel_path = item.get("name") or ""
    path = wait_for_file(rel_path, roots, timeout=timeout)

    initial = min(buffer_bytes, length)
    states = qbit.piece_states(info_hash)
    start_piece = max(0, int(offset + start) // piece_size)
    end_piece = max(
        start_piece,
        (int(offset + start) + max(1, initial) - 1) // piece_size,
    )
    ready_now = (
        len(states) > end_piece
        and all(
            states[piece] == PIECE_READY
            for piece in range(start_piece, end_piece + 1)
        )
    )
    if not ready_now and len(states) > end_piece:
        file_start_piece = max(0, int(offset) // piece_size)
        file_end_piece = max(
            file_start_piece,
            (int(offset) + max(1, size) - 1) // piece_size,
        )
        frontier_piece = file_start_piece
        for piece in range(file_start_piece, min(len(states), file_end_piece + 1)):
            if states[piece] != PIECE_READY:
                break
            frontier_piece = piece + 1
        available = max(0, frontier_piece * piece_size - int(offset))
        if start > available + SEEK_TOLERANCE:
            raise SeekNotReady(start, min(size, available))
    if not wait_pieces(
        qbit, info_hash, offset + start, initial, piece_size, timeout=timeout
    ):
        print(
            f"stream_wait hash={info_hash[:12]} start={start} "
            f"length={initial} offset={offset} timeout={timeout}s",
            flush=True,
        )
        raise StreamTimeout("piezas iniciales no disponibles")

    mime = media_type(str(path))
    file_start_piece = max(0, int(offset) // piece_size)
    file_end_piece = max(
        file_start_piece,
        (int(offset) + max(1, size) - 1) // piece_size,
    )
    is_complete = all(
        states[p] == PIECE_READY
        for p in range(file_start_piece, min(len(states), file_end_piece + 1))
    )
    is_mkv = path.suffix.lower() == ".mkv"
    has_range_header = bool(getattr(handler, "headers", None) and handler.headers.get("Range"))

    if not is_complete and is_mkv and start == 0 and end == size - 1:
        # Modo secuencial puro mientras descarga: mientras el progreso sea menor al 100%
        # y el contenedor sea MKV, sirve como stream secuencial (200 OK con Accept-Ranges: none).
        # Al no anunciar rangos, ffprobe y ffmpeg leen SegmentInfo y Tracks al principio sin
        # saltar al final a buscar Cues, arrancando en segundos.
        handler.send_response(200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Accept-Ranges", "none")
        handler.send_header("Content-Length", str(size))
        handler.send_header("ETag", f'"{info_hash[:20]}-{file_index}"')
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
    else:
        accept_ranges = "bytes" if is_complete else "none"
        handler.send_response(206 if (has_range_header or start > 0 or end < size - 1) else 200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Accept-Ranges", accept_ranges)
        handler.send_header("Content-Length", str(length))
        if has_range_header or start > 0 or end < size - 1:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.send_header("ETag", f'"{info_hash[:20]}-{file_index}"')
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
    if head:
        return

    first_byte_sent = False
    chunk_timeout = max(timeout, STREAM_CHUNK_TIMEOUT) if timeout >= DEFAULT_TIMEOUT else timeout
    with path.open("rb") as fh:
        pos = start
        while pos <= end:
            bytes_into_piece = (offset + pos) % piece_size
            bytes_left_in_piece = piece_size - bytes_into_piece
            chunk = min(CHUNK, bytes_left_in_piece, end - pos + 1)
            if not wait_pieces(
                qbit, info_hash, offset + pos, chunk, piece_size,
                timeout=chunk_timeout,
            ):
                # Ya se enviaron headers: cortamos para no mentir.
                break
            fh.seek(pos)
            data = fh.read(chunk)
            if not data:
                break
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                break
            if not first_byte_sent:
                first_byte_sent = True
                if on_first_byte is not None:
                    try:
                        on_first_byte()
                    except Exception:
                        pass
            pos += len(data)
