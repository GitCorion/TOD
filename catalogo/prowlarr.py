"""Cliente Prowlarr en solo lectura.

Solo hace búsquedas para comprobar salud/peers. No descarga .torrent,
no añade nada a qBittorrent y no toca trackers directamente.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import Config


class ProwlarrError(RuntimeError):
    pass


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60.0):
        if not api_key:
            raise ProwlarrError("sin API key de Prowlarr")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_config(cls, config: Config) -> "ProwlarrClient":
        key = config.prowlarr_api_key
        if not key:
            raise ProwlarrError(
                "sin API key de Prowlarr: configúrala en "
                "TOD → Configuración → Prowlarr"
            )
        return cls(config.prowlarr_url, key)

    def indexers(self) -> list[dict]:
        """Lista los indexers configurados (solo lectura)."""
        url = f"{self.base_url}/indexer"
        request = urllib.request.Request(
            url,
            headers={
                "X-Api-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "catalogo-health/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr no responde: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProwlarrError("Prowlarr devolvió JSON inválido") from exc
        return payload if isinstance(payload, list) else []

    def indexer_stats(self) -> dict:
        """Estadísticas de uso/fallos de los indexers (solo lectura)."""
        url = f"{self.base_url}/indexerstats"
        request = urllib.request.Request(
            url,
            headers={
                "X-Api-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "catalogo-health/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr no responde: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProwlarrError("Prowlarr devolvió JSON inválido") from exc
        return payload if isinstance(payload, dict) else {}

    def set_indexer_enabled(self, indexer_id: int, enabled: bool) -> dict:
        """Activa o pausa un indexer en Prowlarr conservando su configuración."""
        url = f"{self.base_url}/indexer/{int(indexer_id)}"
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "catalogo-health/0.1",
        }
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers),
                timeout=self.timeout,
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr no responde: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProwlarrError("Prowlarr devolvió un indexer inválido")
        payload["enable"] = bool(enabled)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr no responde: {exc}") from exc
        return result if isinstance(result, dict) else {"id": indexer_id, "enable": enabled}

    def search(
        self,
        query: str,
        *,
        media_type: str = "movie",
        limit: int = 50,
        indexer_ids: list[int] | None = None,
    ) -> list[dict]:
        category = "5000" if media_type == "series" else "2000"
        pairs = [
            ("query", query),
            ("type", "search"),
            ("categories", category),
            ("limit", str(limit)),
        ]
        if indexer_ids:
            pairs.extend(
                ("indexerIds", str(indexer_id)) for indexer_id in indexer_ids
            )
        url = f"{self.base_url}/search?{urllib.parse.urlencode(pairs)}"
        request = urllib.request.Request(
            url,
            headers={
                "X-Api-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "catalogo-health/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr no responde: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProwlarrError("Prowlarr devolvió JSON inválido") from exc
        if not isinstance(payload, list):
            return []
        return payload
