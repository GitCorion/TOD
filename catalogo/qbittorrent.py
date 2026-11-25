"""Cliente qBittorrent en solo lectura.

Expone login, versión, lista de torrents, ficheros, propiedades, trackers y
estados de piezas. **No implementa add/delete/pause ni ningún método que
pueda iniciar una descarga.** El backend real se hará aparte y deberá exigir
un `download_intent` aprobado.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import Config


class QbittorrentError(RuntimeError):
    pass


class QbittorrentClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str | None,
        *,
        timeout: float = 15.0,
    ):
        if not password:
            raise QbittorrentError("sin contraseña de qBittorrent (QBIT_PASS)")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self._logged_in = False

    @classmethod
    def from_config(cls, config: Config) -> "QbittorrentClient":
        return cls(
            config.qbittorrent_url,
            config.qbittorrent_user,
            config.qbittorrent_pass,
        )

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> bytes:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        headers = {"Referer": self.base_url, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise QbittorrentError(
                f"qBittorrent HTTP {exc.code} en {path}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise QbittorrentError(f"qBittorrent no responde: {exc}") from exc

    def login(self) -> None:
        raw = self._request(
            "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )
        if raw.decode(errors="replace").strip() != "Ok.":
            raise QbittorrentError("login de qBittorrent rechazado")
        self._logged_in = True

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(2):
            if not self._logged_in:
                self.login()
            try:
                raw = self._request(path, params=params)
                break
            except QbittorrentError as exc:
                if attempt == 0 and "HTTP 403" in str(exc):
                    self._logged_in = False
                    continue
                raise
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QbittorrentError(f"JSON inválido en {path}") from exc

    def app_version(self) -> str:
        for attempt in range(2):
            if not self._logged_in:
                self.login()
            try:
                return self._request("/api/v2/app/version").decode(
                    errors="replace"
                ).strip()
            except QbittorrentError as exc:
                if attempt == 0 and "HTTP 403" in str(exc):
                    self._logged_in = False
                    continue
                raise
        raise QbittorrentError("no se pudo renovar la sesión")

    def torrents(self, category: str | None = None) -> list[dict]:
        params = {"category": category} if category else None
        payload = self._get_json("/api/v2/torrents/info", params=params)
        return payload if isinstance(payload, list) else []

    def torrent(self, info_hash: str) -> dict | None:
        """Consulta un solo torrent por hash (mucho más rápido que listar)."""
        wanted = (info_hash or "").strip().lower()
        if not wanted:
            return None
        payload = self._get_json(
            "/api/v2/torrents/info", params={"hashes": wanted}
        )
        if isinstance(payload, list) and payload:
            return payload[0]
        return None

    def files(self, info_hash: str) -> list[dict]:
        payload = self._get_json(
            "/api/v2/torrents/files", params={"hash": info_hash}
        )
        return payload if isinstance(payload, list) else []

    def properties(self, info_hash: str) -> dict:
        payload = self._get_json(
            "/api/v2/torrents/properties", params={"hash": info_hash}
        )
        return payload if isinstance(payload, dict) else {}

    def piece_states(self, info_hash: str) -> list[int]:
        payload = self._get_json(
            "/api/v2/torrents/pieceStates", params={"hash": info_hash}
        )
        return payload if isinstance(payload, list) else []

    def trackers(self, info_hash: str) -> list[dict]:
        payload = self._get_json(
            "/api/v2/torrents/trackers", params={"hash": info_hash}
        )
        return payload if isinstance(payload, list) else []

    def preferences(self) -> dict[str, Any]:
        payload = self._get_json("/api/v2/app/preferences")
        return payload if isinstance(payload, dict) else {}


def info_hash_from_name(path: str | Path) -> str | None:
    """Utilidad para .torrent: no descarga nada; calcula el hash del info dict."""
    import hashlib

    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw.startswith(b"d"):
        return None
    from .torrent_meta import info_dict_bytes

    info = info_dict_bytes(raw)
    return hashlib.sha1(info).hexdigest() if info else None
