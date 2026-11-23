"""Cliente Prowlarr en solo lectura.

Solo hace búsquedas para comprobar salud/peers. No descarga .torrent,
no añade nada a qBittorrent y no toca trackers directamente.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .config import Config


class ProwlarrError(RuntimeError):
    pass


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_torznab(raw: bytes) -> list[dict]:
    """Convierte la respuesta Torznab de Prowlarr al formato de `search`."""
    if not raw or not raw.lstrip().startswith(b"<"):
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    results: list[dict] = []
    for item in root.iter("item"):
        attrs: dict[str, str] = {}
        for child in item:
            if child.tag.rsplit("}", 1)[-1] == "attr":
                name = (child.get("name") or "").lower()
                if name:
                    attrs[name] = child.get("value") or ""
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        results.append(
            {
                "title": title,
                "size": _int(item.findtext("size") or attrs.get("size")),
                "seeders": _int(attrs.get("seeders")),
                "peers": _int(
                    attrs.get("peers") or attrs.get("leechers")
                ),
                "infoHash": (attrs.get("infohash") or "").lower() or None,
                "downloadUrl": (item.findtext("link") or "").strip(),
                "infoUrl": (item.findtext("comments") or "").strip(),
                "guid": (item.findtext("guid") or title).strip(),
                "tmdbId": _int(attrs.get("tmdbid")),
                "imdbId": _int(attrs.get("imdbid")),
                "category": (item.findtext("category") or "").strip(),
            }
        )
    return results


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60.0):
        if not api_key:
            raise ProwlarrError("sin API key de Prowlarr")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def torznab_base(self) -> str:
        """Raíz de los feeds Torznab: Prowlarr expone /{id}/api."""
        base = self.base_url.rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return base

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


    def torznab_search(
        self,
        indexer_id: int,
        *,
        media_type: str = "movie",
        query: str = "",
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        imdb_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Búsqueda Torznab por ID, como la que usan Radarr y Sonarr.

        La API JSON de Prowlarr solo acepta texto. El proxy Torznab sí
        entiende `tmdbid`/`tvdbid`/`imdbid`, y muchos trackers lo usan para
        devolver el título aunque su nombre local no coincida.
        """
        params = [
            ("apikey", self.api_key),
            ("t", "movie" if media_type == "movie" else "tvsearch"),
            ("cat", "2000" if media_type == "movie" else "5000"),
            ("limit", str(max(1, int(limit)))),
        ]
        if query:
            params.append(("q", query))
        if tmdb_id:
            params.append(("tmdbid", str(int(tmdb_id))))
        if tvdb_id:
            params.append(("tvdbid", str(int(tvdb_id))))
        if imdb_id:
            params.append(("imdbid", str(imdb_id)))
        url = (
            f"{self.torznab_base}/{int(indexer_id)}/api?"
            f"{urllib.parse.urlencode(params)}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/xml",
                "User-Agent": "catalogo-torznab/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr Torznab HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProwlarrError(f"Prowlarr Torznab no responde: {exc}") from exc
        return parse_torznab(raw)
