"""Pruebas de conexión de las integraciones de TOD.

Todas las funciones son de solo lectura: consultan versión, estado o número
de indexers. No añaden torrents, no descargan `.torrent` ni modifican nada.
Se usan desde la pantalla de configuración y desde `GET /api/system`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .app_settings import path_status
from .arr import RadarrClient, SonarrClient
from .config import Config
from .jackett import JackettError
from .prowlarr import ProwlarrClient
from .qbittorrent import QbittorrentClient

DEFAULT_TIMEOUT = 15.0


class IntegrationError(RuntimeError):
    pass


def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "tod-integrations/0.1",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise IntegrationError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(f"no responde: {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntegrationError("respuesta JSON inválida") from exc


def _result(name: str, started: float, **data: Any) -> dict[str, Any]:
    return {
        "name": name,
        "ok": True,
        "latency_ms": int((time.monotonic() - started) * 1000),
        **data,
    }


def _error(name: str, started: float, exc: Exception) -> dict[str, Any]:
    return {
        "name": name,
        "ok": False,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": str(exc)[:300],
    }


def test_prowlarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        client = ProwlarrClient.from_config(config)
        indexers = client.indexers()
        return _result(
            "prowlarr",
            started,
            detail=f"{len(indexers)} indexers",
            items=len(indexers),
        )
    except Exception as exc:
        return _error("prowlarr", started, exc)


def test_jackett(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.jackett_api_key:
            raise JackettError("sin API key de Jackett")
        # El listado /indexers exige sesión de la UI. El endpoint Torznab
        # valida la API key y confirma que Jackett responde.
        url = (
            f"{config.jackett_url.rstrip('/')}/indexers/all/results/"
            "torznab/api?"
            + urllib.parse.urlencode(
                {"apikey": config.jackett_api_key, "t": "caps"}
            )
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/rss+xml, application/xml",
                "User-Agent": "tod-integrations/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                raw = response.read(4096)
        except urllib.error.HTTPError as exc:
            raise IntegrationError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IntegrationError(f"no responde: {exc}") from exc
        text = raw.decode("utf-8", errors="ignore")
        if "<caps" not in text and "<rss" not in text:
            raise IntegrationError("respuesta Torznab inválida")
        return _result("jackett", started, detail="Jackett responde (Torznab)")
    except Exception as exc:
        return _error("jackett", started, exc)


def test_qbittorrent(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.qbittorrent_pass:
            raise IntegrationError("sin contraseña de qBittorrent")
        client = QbittorrentClient.from_config(config)
        version = client.app_version()
        torrents = client.torrents()
        return _result(
            "qbittorrent",
            started,
            detail=f"qBittorrent {version}, {len(torrents)} torrents",
            version=version,
            items=len(torrents),
        )
    except Exception as exc:
        return _error("qbittorrent", started, exc)


def test_jellyfin(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.jellyfin_api_key:
            raise IntegrationError("sin API key de Jellyfin")
        payload = _get_json(
            f"{config.jellyfin_url.rstrip('/')}/System/Info",
            headers={
                "Authorization": (
                    f'MediaBrowser Token="{config.jellyfin_api_key}"'
                )
            },
        )
        name = (payload or {}).get("ServerName") or "Jellyfin"
        version = (payload or {}).get("Version") or ""
        return _result(
            "jellyfin",
            started,
            detail=f"{name} {version}".strip(),
            version=version,
        )
    except Exception as exc:
        return _error("jellyfin", started, exc)


def test_radarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.radarr_api_key:
            raise IntegrationError("sin API key de Radarr")
        status = RadarrClient.from_config(config).status()
        return _result(
            "radarr",
            started,
            detail=str(status.get("version") or "Radarr"),
            version=str(status.get("version") or ""),
        )
    except Exception as exc:
        return _error("radarr", started, exc)


def test_sonarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.sonarr_api_key:
            raise IntegrationError("sin API key de Sonarr")
        status = SonarrClient.from_config(config).status()
        return _result(
            "sonarr",
            started,
            detail=str(status.get("version") or "Sonarr"),
            version=str(status.get("version") or ""),
        )
    except Exception as exc:
        return _error("sonarr", started, exc)


def test_tmdb(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.tmdb_api_key:
            raise IntegrationError("sin API key de TMDB")
        _get_json(
            "https://api.themoviedb.org/3/configuration?"
            + urllib.parse.urlencode({"api_key": config.tmdb_api_key})
        )
        return _result("tmdb", started, detail="TMDB responde")
    except Exception as exc:
        return _error("tmdb", started, exc)


def test_realdebrid(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.rd_api_key:
            raise IntegrationError("sin API key de Real-Debrid")
        payload = _get_json(
            "https://api.real-debrid.com/rest/1.0/user",
            headers={"Authorization": f"Bearer {config.rd_api_key}"},
        )
        username = (payload or {}).get("username") or ""
        return _result(
            "realdebrid",
            started,
            detail=f"Real-Debrid {username}".strip(),
        )
    except Exception as exc:
        return _error("realdebrid", started, exc)


def test_directories(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    paths = {
        "catalog_library": config.catalog_library,
        "library_root": config.library_root,
        "seed_root": config.seed_root,
        "downloads_root": config.downloads_root,
        "ondemand_save_path": config.ondemand_save_path,
    }
    checks = {
        label: path_status(value) for label, value in paths.items()
    }
    missing = [
        label
        for label, status in checks.items()
        if not status.get("exists") or not status.get("writable")
    ]
    return _result(
        "directories",
        started,
        ok=not missing,
        detail=(
            "todas las rutas existen y son escribibles"
            if not missing
            else "revisar: " + ", ".join(missing)
        ),
        paths=checks,
    )


TESTS = {
    "prowlarr": test_prowlarr,
    "jackett": test_jackett,
    "qbittorrent": test_qbittorrent,
    "jellyfin": test_jellyfin,
    "radarr": test_radarr,
    "sonarr": test_sonarr,
    "tmdb": test_tmdb,
    "realdebrid": test_realdebrid,
    "directories": test_directories,
}

ALIASES = {
    "qbit": "qbittorrent",
    "real-debrid": "realdebrid",
    "rd": "realdebrid",
    "paths": "directories",
}


def test_integration(name: str, config: Config) -> dict[str, Any]:
    key = ALIASES.get(name, name)
    handler = TESTS.get(key)
    if handler is None:
        raise IntegrationError(f"integración desconocida: {name}")
    return handler(config)
