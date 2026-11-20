"""Bencode mínimo para calcular el info_hash de un .torrent local.

No descarga nada. Sirve para identificar un torrent sin confiar en el nombre.
"""

from __future__ import annotations

from typing import Any


class BencodeError(ValueError):
    pass


def _decode(data: bytes, index: int) -> tuple[Any, int]:
    if index >= len(data):
        raise BencodeError("fin inesperado")
    marker = data[index : index + 1]
    if marker == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1 : end]), end + 1
    if marker == b"l":
        index += 1
        values = []
        while data[index : index + 1] != b"e":
            value, index = _decode(data, index)
            values.append(value)
        return values, index + 1
    if marker == b"d":
        index += 1
        result = {}
        while data[index : index + 1] != b"e":
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            result[key] = value
        return result, index + 1
    if marker.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        start = colon + 1
        end = start + length
        return data[start:end], end
    raise BencodeError(f"marcador inválido: {marker!r}")


def info_dict_bytes(raw: bytes) -> bytes | None:
    """Devuelve los bytes exactos del diccionario `info` de un .torrent."""
    if not raw.startswith(b"d"):
        return None
    index = 1
    try:
        while index < len(raw) and raw[index : index + 1] != b"e":
            key, index = _decode(raw, index)
            start = index
            _, index = _decode(raw, index)
            if key == b"info":
                return raw[start:index]
    except (BencodeError, ValueError, IndexError):
        return None
    return None
