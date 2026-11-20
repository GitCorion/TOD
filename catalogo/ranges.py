"""Parseo de Range RFC 7233 para un único rango.

Casos que el prototipo actual hacía mal:
  - bytes=-1000  -> debía ser los últimos 1000 bytes, no los primeros.
  - bytes=999999999- -> 416, no 206 con longitud negativa.
  - cabecera inválida -> se ignora y se sirve 200 completo.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RangeResult:
    status: int
    start: int | None
    end: int | None
    length: int
    content_range: str | None = None
    header_present: bool = False

    @property
    def is_partial(self) -> bool:
        return self.status == 206


def _full(size: int, header_present: bool) -> RangeResult:
    if size <= 0:
        return RangeResult(200, 0, -1, 0, None, header_present)
    return RangeResult(200, 0, size - 1, size, None, header_present)


def _unsatisfiable(size: int, header_present: bool = True) -> RangeResult:
    return RangeResult(416, None, None, 0, f"bytes */{size}", header_present)


def parse_range(header: str | None, size: int) -> RangeResult:
    if size < 0:
        raise ValueError("tamaño negativo")

    if header is None or not header.strip():
        return _full(size, False)

    raw = header.strip()
    if not raw.lower().startswith("bytes="):
        return _full(size, True)

    spec = raw[len("bytes=") :].strip()
    if not spec or "," in spec or "-" not in spec:
        return _full(size, True)

    left, right = spec.split("-", 1)
    left = left.strip()
    right = right.strip()
    if not left and not right:
        return _full(size, True)

    try:
        if not left:
            suffix = int(right)
            if suffix <= 0 or size == 0:
                return _unsatisfiable(size)
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(left)
            if start < 0:
                return _full(size, True)
            if size == 0 or start >= size:
                return _unsatisfiable(size)
            if right:
                end = int(right)
                if end < start:
                    return _full(size, True)
                end = min(end, size - 1)
            else:
                end = size - 1
    except ValueError:
        return _full(size, True)

    length = end - start + 1
    return RangeResult(
        206,
        start,
        end,
        length,
        f"bytes {start}-{end}/{size}",
        True,
    )
