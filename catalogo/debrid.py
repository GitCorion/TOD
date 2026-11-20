"""Real-Debrid para públicos: add-and-check, sin instantAvailability.

Solo se usa con releases `privacy='public'`. Nunca se llama con un magnet
privado. El enlace directo se guarda en la base, no se expone en /resolve.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Config

BASE = "https://api.real-debrid.com/rest/1.0"


class DebridError(RuntimeError):
    pass


@dataclass(frozen=True)
class DebridCache:
    cached: bool
    link: str | None = None
    torrent_id: str | None = None
    status: str | None = None
    error: str | None = None


class RealDebridClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE,
        timeout: float = 30.0,
        min_interval: float = 0.3,
    ):
        if not api_key:
            raise DebridError("sin RD_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_call = 0.0

    @classmethod
    def from_config(cls, config: Config) -> "RealDebridClient":
        return cls(config.rd_api_key or "")

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, str] | None = None,
    ) -> Any:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "catalogo-debrid/0.1",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise DebridError(f"RD HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DebridError(f"RD no responde: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DebridError("RD devolvió JSON inválido") from exc

    def user(self) -> dict:
        return self._request("GET", "/user")

    def add_magnet(self, infohash: str) -> str:
        magnet = f"magnet:?xt=urn:btih:{infohash}"
        payload = self._request("POST", "/torrents/addMagnet", {"magnet": magnet})
        if not isinstance(payload, dict) or not payload.get("id"):
            raise DebridError("RD no devolvió id de torrent")
        return str(payload["id"])

    def info(self, torrent_id: str) -> dict:
        payload = self._request("GET", f"/torrents/info/{torrent_id}")
        return payload if isinstance(payload, dict) else {}

    def select_all(self, torrent_id: str) -> None:
        self._request(
            "POST", f"/torrents/selectFiles/{torrent_id}", {"files": "all"}
        )

    def delete(self, torrent_id: str) -> None:
        try:
            self._request("DELETE", f"/torrents/delete/{torrent_id}")
        except DebridError:
            pass

    def unrestrict(self, link: str) -> str | None:
        payload = self._request("POST", "/unrestrict/link", {"link": link})
        if isinstance(payload, dict):
            return payload.get("download")
        return None

    def check_torrent(
        self,
        infohash: str,
        *,
        timeout: float = 12.0,
    ) -> DebridCache:
        """Añade el magnet, comprueba si está cacheado y devuelve un enlace.

        Si no está cacheado, borra el torrent de RD inmediatamente.
        """
        try:
            torrent_id = self.add_magnet(infohash)
        except DebridError as exc:
            return DebridCache(False, error=str(exc))

        deadline = time.time() + max(1.0, timeout)
        try:
            selected = False
            while time.time() < deadline:
                info = self.info(torrent_id)
                status = (info.get("status") or "").lower()
                if status in ("magnet_conversion", "queued", "downloading"):
                    time.sleep(1.0)
                    continue
                if status in ("waiting_files_selection", "downloaded"):
                    if not selected and status == "waiting_files_selection":
                        self.select_all(torrent_id)
                        selected = True
                    if status == "waiting_files_selection" or not info.get("links"):
                        time.sleep(1.0)
                        continue
                    links = info.get("links") or []
                    if not links:
                        time.sleep(1.0)
                        continue
                    direct = self.unrestrict(str(links[0]))
                    if not direct:
                        self.delete(torrent_id)
                        return DebridCache(False, status=status,
                                           error="sin enlace directo")
                    return DebridCache(True, direct, torrent_id, status)
                if status in ("error", "virus", "dead"):
                    self.delete(torrent_id)
                    return DebridCache(False, status=status, error=status)
                time.sleep(1.0)
        except DebridError as exc:
            self.delete(torrent_id)
            return DebridCache(False, error=str(exc))

        self.delete(torrent_id)
        return DebridCache(False, status="timeout", error="no cacheado")
