"""Pruebas de conexión de las integraciones de TOD.

Todas las funciones son de solo lectura: consultan versión, estado o número
de indexers. No añaden torrents, no descargan `.torrent` ni modifican nada.
Se usan desde la pantalla de configuración y desde `GET /api/system`.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
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
LIGHT_TIMEOUT = 8.0


class IntegrationError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "unknown", hint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.hint = hint


SECRET_RE = re.compile(
    r"(?i)\b(apikey|api_key|token|pass|password|rd_api_key)=([^&\s]+)"
)
HTTP_HINTS = {
    401: ("auth", "La clave o contraseña no es válida, o no tiene permisos."),
    403: ("auth", "El servicio rechaza la clave o la sesión. Revisa credenciales y permisos."),
    404: ("not_found", "La URL responde, pero no es la API esperada. Revisa la ruta base."),
    429: ("rate_limit", "El servicio limita las peticiones. Espera un momento y reintenta."),
}
NETWORK_HINTS = {
    "refused": "Conexión rechazada. Revisa el puerto y que el servicio esté activo.",
    "dns": "El nombre del host no se resuelve. Revisa la URL.",
    "timeout": "Sin respuesta a tiempo. Revisa la URL, la red y el firewall.",
    "tls": "El certificado TLS no es válido para ese host.",
    "unreachable": "No se puede alcanzar el servicio. Revisa la URL y la red.",
}


def _safe(message: str) -> str:
    """Quita claves y tokens antes de devolver un error al navegador."""
    return SECRET_RE.sub(r"\1=***", message)


def _http_hint(code: int) -> tuple[str, str]:
    if code in HTTP_HINTS:
        return HTTP_HINTS[code]
    if 500 <= code <= 599:
        return "server", "El servicio responde con un error interno. Revisa sus logs."
    return "http", f"El servicio respondió HTTP {code}. Revisa URL, clave y permisos."


def _network_hint(exc: BaseException) -> tuple[str, str]:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError):
        return "tls", NETWORK_HINTS["tls"]
    if isinstance(reason, socket.gaierror):
        return "dns", NETWORK_HINTS["dns"]
    if isinstance(reason, ConnectionRefusedError):
        return "refused", NETWORK_HINTS["refused"]
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout", NETWORK_HINTS["timeout"]
    return "unreachable", NETWORK_HINTS["unreachable"]


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Devuelve (tipo, pista) para que la interfaz sepa qué revisar."""
    if isinstance(exc, IntegrationError) and exc.kind != "unknown":
        return exc.kind, exc.hint

    text = str(exc)
    match = re.search(r"HTTP (\d{3})", text)
    if match:
        return _http_hint(int(match.group(1)))

    if isinstance(exc, urllib.error.HTTPError):
        return _http_hint(exc.code)
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return _network_hint(exc)
    if isinstance(exc, ssl.SSLError):
        return "tls", NETWORK_HINTS["tls"]
    if isinstance(exc, json.JSONDecodeError):
        return (
            "not_api",
            "La URL responde, pero no es la API esperada. Revisa la ruta base.",
        )

    low = text.lower()
    if "sin api key" in low or "sin contraseña" in low or "sin password" in low:
        return "missing", "Falta la clave o contraseña. Añádela en Configuración → Integraciones."
    if "json inválido" in low or "json invalido" in low or "respuesta json" in low:
        return (
            "not_api",
            "La URL responde, pero no es la API esperada. Revisa la ruta base.",
        )
    if "connection refused" in low or "conexión rechazada" in low:
        return "refused", NETWORK_HINTS["refused"]
    if "no responde" in low:
        return "unreachable", NETWORK_HINTS["unreachable"]
    if "timeout" in low or "timed out" in low:
        return "timeout", NETWORK_HINTS["timeout"]
    return "unknown", "Revisa la URL, la clave y que el servicio esté activo."


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
        kind, hint = _http_hint(exc.code)
        raise IntegrationError(f"HTTP {exc.code}", kind=kind, hint=hint) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        kind, hint = _network_hint(exc)
        raise IntegrationError(f"no responde: {exc}", kind=kind, hint=hint) from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            "respuesta JSON inválida",
            kind="not_api",
            hint="La URL responde, pero no es la API esperada. Revisa la ruta base.",
        ) from exc


def _result(name: str, started: float, **data: Any) -> dict[str, Any]:
    return {
        "name": name,
        "ok": True,
        "latency_ms": int((time.monotonic() - started) * 1000),
        **data,
    }


def _error(name: str, started: float, exc: Exception) -> dict[str, Any]:
    kind, hint = classify_error(exc)
    return {
        "name": name,
        "ok": False,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": _safe(str(exc))[:300],
        "kind": kind,
        "hint": hint,
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


def test_jackett(
    config: Config, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
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
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(4096)
        except urllib.error.HTTPError as exc:
            kind, hint = _http_hint(exc.code)
            raise IntegrationError(f"HTTP {exc.code}", kind=kind, hint=hint) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            kind, hint = _network_hint(exc)
            raise IntegrationError(f"no responde: {exc}", kind=kind, hint=hint) from exc
        text = raw.decode("utf-8", errors="ignore")
        if "<caps" not in text and "<rss" not in text:
            raise IntegrationError(
                "respuesta Torznab inválida",
                kind="not_api",
                hint="Jackett responde, pero no con Torznab. Revisa la URL base.",
            )
        return _result("jackett", started, detail="Jackett responde (Torznab)")
    except Exception as exc:
        return _error("jackett", started, exc)


def test_qbittorrent(
    config: Config, *, light: bool = False
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.qbittorrent_pass:
            raise IntegrationError(
                "sin contraseña de qBittorrent",
                kind="missing",
                hint="Añade la contraseña de qBittorrent en Configuración → Integraciones.",
            )
        client = (
            QbittorrentClient(
                config.qbittorrent_url,
                config.qbittorrent_user,
                config.qbittorrent_pass,
                timeout=LIGHT_TIMEOUT,
            )
            if light
            else QbittorrentClient.from_config(config)
        )
        version = client.app_version()
        if light:
            return _result(
                "qbittorrent", started, detail=f"qBittorrent {version}", version=version
            )
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


def test_jellyfin(
    config: Config, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
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
            timeout=timeout,
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


def test_tmdb(
    config: Config, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.tmdb_api_key:
            raise IntegrationError("sin API key de TMDB")
        _get_json(
            "https://api.themoviedb.org/3/configuration?"
            + urllib.parse.urlencode({"api_key": config.tmdb_api_key}),
            timeout=timeout,
        )
        return _result("tmdb", started, detail="TMDB responde")
    except Exception as exc:
        return _error("tmdb", started, exc)


def test_realdebrid(
    config: Config, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.rd_api_key:
            raise IntegrationError("sin API key de Real-Debrid")
        payload = _get_json(
            "https://api.real-debrid.com/rest/1.0/user",
            headers={"Authorization": f"Bearer {config.rd_api_key}"},
            timeout=timeout,
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
        "seed_root": config.seed_root,
        "downloads_root": config.downloads_root,
        "ondemand_save_path": config.ondemand_save_path,
    }
    if config.library_root_movies:
        paths["library_root_movies"] = config.library_root_movies
    if config.library_root_series:
        paths["library_root_series"] = config.library_root_series
    if not (config.library_root_movies or config.library_root_series):
        paths["library_root"] = config.library_root
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

def _light_prowlarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.prowlarr_api_key:
            raise IntegrationError("sin API key de Prowlarr")
        client = ProwlarrClient(
            config.prowlarr_url,
            config.prowlarr_api_key,
            timeout=LIGHT_TIMEOUT,
        )
        indexers = client.indexers()
        return _result(
            "prowlarr",
            started,
            detail=f"{len(indexers)} indexers",
            items=len(indexers),
        )
    except Exception as exc:
        return _error("prowlarr", started, exc)


def _light_radarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.radarr_api_key:
            raise IntegrationError("sin API key de Radarr")
        status = RadarrClient(
            config.radarr_url,
            config.radarr_api_key,
            timeout=LIGHT_TIMEOUT,
        ).status()
        return _result(
            "radarr",
            started,
            detail=str(status.get("version") or "Radarr"),
            version=str(status.get("version") or ""),
        )
    except Exception as exc:
        return _error("radarr", started, exc)


def _light_sonarr(config: Config) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not config.sonarr_api_key:
            raise IntegrationError("sin API key de Sonarr")
        status = SonarrClient(
            config.sonarr_url,
            config.sonarr_api_key,
            timeout=LIGHT_TIMEOUT,
        ).status()
        return _result(
            "sonarr",
            started,
            detail=str(status.get("version") or "Sonarr"),
            version=str(status.get("version") or ""),
        )
    except Exception as exc:
        return _error("sonarr", started, exc)


LIGHT_TESTS = {
    **TESTS,
    "qbittorrent": lambda config: test_qbittorrent(config, light=True),
    "prowlarr": _light_prowlarr,
    "jackett": lambda config: test_jackett(config, timeout=LIGHT_TIMEOUT),
    "jellyfin": lambda config: test_jellyfin(config, timeout=LIGHT_TIMEOUT),
    "radarr": _light_radarr,
    "sonarr": _light_sonarr,
    "tmdb": lambda config: test_tmdb(config, timeout=LIGHT_TIMEOUT),
    "realdebrid": lambda config: test_realdebrid(config, timeout=LIGHT_TIMEOUT),
}

ALIASES = {
    "qbit": "qbittorrent",
    "real-debrid": "realdebrid",
    "rd": "realdebrid",
    "paths": "directories",
}


def test_integration(
    name: str, config: Config, *, light: bool = False
) -> dict[str, Any]:
    key = ALIASES.get(name, name)
    handler = (LIGHT_TESTS if light else TESTS).get(key)
    if handler is None:
        raise IntegrationError(f"integración desconocida: {name}")
    return handler(config)
