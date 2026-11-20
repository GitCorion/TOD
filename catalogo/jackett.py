"""Cliente Jackett en solo lectura para el fallback público.

No descarga .torrent. La API key se usa en memoria y se elimina de las URLs
que se guardan en la base.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .config import Config

TORZNAB = "{http://torznab.com/schemas/2015/feed}"


class JackettError(RuntimeError):
    pass


def sanitize_link(link: str) -> str:
    """Quita jackett_apikey de una URL de descarga antes de persistirla."""
    if not link:
        return ""
    parts = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k.lower() != "jackett_apikey"]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path,
         urllib.parse.urlencode(query), parts.fragment)
    )


def with_api_key(link: str, api_key: str) -> str:
    if not link:
        return ""
    parts = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k.lower() != "jackett_apikey"]
    query.append(("jackett_apikey", api_key))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path,
         urllib.parse.urlencode(query), parts.fragment)
    )


def parse_results(raw: bytes, indexer: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise JackettError("respuesta XML inválida") from exc
    error = root.find("error")
    if error is not None:
        description = error.get("description") or error.text or "error"
        raise JackettError(description[:300])
    results: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        attrs = {
            attr.get("name"): attr.get("value")
            for attr in item.findall(f"{TORZNAB}attr")
        }
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        if not title or not link:
            continue
        try:
            size = int(item.findtext("size") or 0)
        except ValueError:
            size = 0
        try:
            tmdb = int(attrs.get("tmdbid") or 0)
        except ValueError:
            tmdb = 0
        jackett_indexer = item.find("jackettindexer")
        indexer_name = (
            (jackett_indexer.text or "").strip()
            if jackett_indexer is not None
            else ""
        ) or indexer
        indexer_id = (
            jackett_indexer.get("id") if jackett_indexer is not None else None
        ) or indexer
        privacy = (item.findtext("type") or "").strip().lower()
        results.append(
            {
                "indexer": indexer_name,
                "indexer_id": indexer_id,
                "privacy": privacy,
                "title": title,
                "size": size,
                "seeders": int(attrs.get("seeders") or 0),
                "peers": int(attrs.get("peers") or 0),
                "infohash": attrs.get("infohash") or "",
                "magnet": attrs.get("magneturl") or "",
                "link": link,
                "guid": item.findtext("guid") or "",
                "publish_date": item.findtext("pubDate") or "",
                "tmdbId": tmdb,
                "category": attrs.get("category")
                or item.findtext("category")
                or "",
            }
        )
    return results


class JackettClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 45.0):
        if not api_key:
            raise JackettError("sin API key de Jackett (JACKETT_API_KEY)")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_config(cls, config: Config) -> "JackettClient":
        return cls(config.jackett_url, config.jackett_api_key or "")

    def fetch_torrent(self, link: str, *, timeout: float = 45.0) -> bytes:
        """Descarga el .torrent (metadatos) y lo devuelve en memoria."""
        url = link if "jackett_apikey=" in link else with_api_key(link, self.api_key)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "catalogo-public-fallback/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise JackettError(f"Jackett HTTP {exc.code} al bajar .torrent") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JackettError(f"Jackett no responde al bajar .torrent: {exc}") from exc
        if not raw.startswith(b"d"):
            raise JackettError("el enlace no devolvió un .torrent")
        return raw

    def search(
        self,
        indexer: str,
        query: str,
        *,
        media_type: str = "movie",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        # Jackett/DonTorrent devuelve 400 con cat=2000; el filtrado por
        # título/año se hace después en public_sync.
        params = {
            "apikey": self.api_key,
            "t": "search",
            "cat": "",
            "q": query,
        }
        url = (
            f"{self.base_url}/indexers/{urllib.parse.quote(indexer)}"
            f"/results/torznab/api?{urllib.parse.urlencode(params)}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "catalogo-public-fallback/0.1",
                "Accept": "application/rss+xml, application/xml",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise JackettError(f"Jackett HTTP {exc.code} en {indexer}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JackettError(f"Jackett no responde en {indexer}: {exc}") from exc
        return parse_results(raw, indexer)[:limit]
