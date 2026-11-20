"""Streaming HTTP desde un fichero parcial de qBittorrent.

- Comprueba piezas globales del torrent (incluye offset de packs).
- Seek corto: espera piezas hasta timeout; seek lejano: 503 antes de headers.
- Busca el fichero en temp, save y 1fichier.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Protocol

from .config import media_type

CHUNK = 1024 * 1024
BUFFER = 4 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
PIECE_READY = 2

STREAM_ROOTS = [
    "/mnt/cache/tmp/ondemand",
    "/mnt/cache/tmp",
    "/mnt/descargas/ondemand",
    "/mnt/descargas/catalogo",
    "/mnt/descargas/radarr",
    "/mnt/descargas/tv-sonarr",
    "/mnt/fichier/descargas",
]


class StreamTimeout(RuntimeError):
    pass


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
    if not wait_pieces(
        qbit, info_hash, offset + start, initial, piece_size, timeout=timeout
    ):
        raise StreamTimeout("piezas iniciales no disponibles")

    mime = media_type(str(path))
    handler.send_response(206)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("ETag", f'"{info_hash[:20]}-{file_index}"')
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if head:
        return

    with path.open("rb") as fh:
        pos = start
        while pos <= end:
            chunk = min(CHUNK, end - pos + 1)
            if not wait_pieces(
                qbit, info_hash, offset + pos, chunk, piece_size,
                timeout=timeout,
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
            pos += len(data)
