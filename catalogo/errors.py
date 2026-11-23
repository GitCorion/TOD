"""Errores que la API traduce a respuestas HTTP."""

from __future__ import annotations

from typing import Any


class WebError(RuntimeError):
    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
