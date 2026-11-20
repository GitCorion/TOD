"""Escritura controlada en qBittorrent.

Este cliente solo debe usarse desde `backend.prepare_download`, que exige
un `play_intent` y un `seed_commitment` aprobado. No hay atajos.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from typing import Iterable

from .qbittorrent import QbittorrentClient, QbittorrentError


class QbittorrentWriteClient(QbittorrentClient):
    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _post_multipart(
        self,
        path: str,
        fields: dict[str, str],
        *,
        filename: str,
        content: bytes,
    ) -> str:
        self._ensure_login()
        boundary = f"----catalogo{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode()
            )
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="torrents"; '
                f'filename="{filename}"\r\n'
                "Content-Type: application/x-bittorrent\r\n\r\n"
            ).encode()
        )
        chunks.append(content)
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={
                "Referer": self.base_url,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        for attempt in range(2):
            self._ensure_login()
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.read().decode(errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 403 and attempt == 0:
                    self._logged_in = False
                    continue
                raise QbittorrentError(
                    f"qBittorrent HTTP {exc.code} en {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise QbittorrentError(
                    f"qBittorrent no responde: {exc}"
                ) from exc
        raise QbittorrentError("no se pudo renovar la sesión")

    def add_torrent(
        self,
        torrent_bytes: bytes,
        *,
        save_path: str,
        category: str = "ondemand",
        paused: bool = True,
        sequential: bool = True,
        first_last: bool = True,
        use_download_path: bool = False,
        tags: str | None = None,
        filename: str = "catalogo.torrent",
    ) -> str:
        if not torrent_bytes.startswith(b"d"):
            raise QbittorrentError("el fichero no parece un .torrent")
        fields = {
            "savepath": save_path,
            "category": category,
            "paused": "true" if paused else "false",
            "sequentialDownload": "true" if sequential else "false",
            "firstLastPiecePrio": "true" if first_last else "false",
            "autoTMM": "false",
            "useDownloadPath": "true" if use_download_path else "false",
        }
        if tags:
            fields["tags"] = tags
        result = self._post_multipart(
            "/api/v2/torrents/add",
            fields,
            filename=filename,
            content=torrent_bytes,
        )
        if result.strip() not in ("Ok.", ""):
            raise QbittorrentError(f"qBittorrent add: {result[:120]}")
        return result

    def _form(self, path: str, data: dict[str, str]) -> str:
        for attempt in range(2):
            self._ensure_login()
            try:
                raw = self._request(path, data=data)
                return raw.decode(errors="replace")
            except QbittorrentError as exc:
                if attempt == 0 and "HTTP 403" in str(exc):
                    self._logged_in = False
                    continue
                raise
        raise QbittorrentError("no se pudo renovar la sesión")

    def set_file_priorities(
        self,
        info_hash: str,
        priorities: dict[int, int],
    ) -> None:
        """qBittorrent solo acepta un priority por llamada; agrupamos."""
        grouped: dict[int, list[int]] = {}
        for file_index, priority in priorities.items():
            grouped.setdefault(int(priority), []).append(int(file_index))
        for priority, ids in grouped.items():
            self._form(
                "/api/v2/torrents/filePrio",
                {
                    "hash": info_hash,
                    "id": "|".join(str(i) for i in ids),
                    "priority": str(priority),
                },
            )

    def set_force_start(self, info_hash: str, value: bool = True) -> None:
        self._form(
            "/api/v2/torrents/setForceStart",
            {"hashes": info_hash, "value": "true" if value else "false"},
        )

    def set_download_limit(self, info_hash: str, limit: int) -> None:
        """Límite de descarga en bytes/s; -1 o 0 = ilimitado."""
        self._form(
            "/api/v2/torrents/setDownloadLimit",
            {"hashes": info_hash, "limit": str(int(limit))},
        )

    def set_download_limit_many(
        self, info_hashes: list[str], limit: int
    ) -> None:
        if not info_hashes:
            return
        self._form(
            "/api/v2/torrents/setDownloadLimit",
            {"hashes": "|".join(info_hashes), "limit": str(int(limit))},
        )

    def resume(self, info_hash: str) -> None:
        self._form("/api/v2/torrents/start", {"hashes": info_hash})

    def pause(self, info_hash: str) -> None:
        self._form("/api/v2/torrents/stop", {"hashes": info_hash})

    def recheck(self, info_hash: str) -> None:
        self._form("/api/v2/torrents/recheck", {"hashes": info_hash})

    def delete(self, info_hash: str, *, delete_files: bool = False) -> None:
        self._form(
            "/api/v2/torrents/delete",
            {
                "hashes": info_hash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    def set_category(self, info_hash: str, category: str) -> None:
        self._form(
            "/api/v2/torrents/setCategory",
            {"hashes": info_hash, "category": category},
        )

    def set_location(self, info_hash: str, location: str) -> None:
        self._form(
            "/api/v2/torrents/setLocation",
            {"hashes": info_hash, "location": location},
        )
