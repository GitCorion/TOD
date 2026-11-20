"""Interfaz del motor de torrents. En esta fase está deliberadamente apagada.

El gateway puede pedir un `Resolution(source='torrent')`, pero si el backend
no está activo devuelve 503 sin tocar qBittorrent ni la red.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Protocol


class TorrentBackendDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class TorrentHandle:
    info_hash: str
    file_index: int | None
    size: int | None = None
    save_path: str | None = None
    state: str | None = None


class TorrentBackend(Protocol):
    def ensure(self, release: Any, file_index: int | None = None) -> TorrentHandle:
        ...

    def wait_range(self, handle: TorrentHandle, offset: int, length: int,
                   timeout: float = 30.0) -> None:
        ...

    def read_range(self, handle: TorrentHandle, offset: int, length: int) -> Iterator[bytes]:
        ...


class DisabledBackend:
    """Backend nulo: no arranca nada y falla de forma explícita."""

    def ensure(self, release: Any, file_index: int | None = None) -> TorrentHandle:
        raise TorrentBackendDisabled(
            "motor de torrents desactivado en esta fase (construcción en la sombra)"
        )

    def wait_range(self, handle: TorrentHandle, offset: int, length: int,
                   timeout: float = 30.0) -> None:
        raise TorrentBackendDisabled("motor de torrents desactivado")

    def read_range(self, handle: TorrentHandle, offset: int, length: int) -> Iterator[bytes]:
        raise TorrentBackendDisabled("motor de torrents desactivado")
        yield b""  # pragma: no cover
