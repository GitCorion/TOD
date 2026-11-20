"""Ajustes persistentes de TOD (Torrent on Demand).

La configuración deja de depender solo de variables de entorno. La web guarda
los valores en la tabla `settings` y esta capa construye un `Config` efectivo
con esta precedencia:

    base de datos > entorno/.env > valores por defecto

Los secretos nunca se devuelven al navegador: solo se indica si están
configurados y una pista corta. Al guardar, un secreto vacío significa "no
cambiar"; para borrarlo hay que pedirlo explícitamente en `clear`.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import Config

SECTION_LABELS = {
    "integraciones": "Integraciones",
    "directorios": "Directorios",
    "reproduccion": "Reproducción",
    "seguridad": "Seguridad",
}


@dataclass(frozen=True)
class Setting:
    key: str
    attr: str
    section: str
    label: str
    kind: str = "text"
    env: str | None = None
    default: Any = ""
    secret: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    help: str = ""
    restart_required: bool = False


# El orden importa: es el que verá la pantalla de configuración.
SETTINGS: tuple[Setting, ...] = (
    # Integraciones
    Setting(
        "prowlarr_url", "prowlarr_url", "integraciones", "Prowlarr · URL",
        kind="url", env="PROWLARR_URL",
        default="http://127.0.0.1:9696/api/v1",
        help="Base de la API v1 de Prowlarr.",
    ),
    Setting(
        "prowlarr_api_key", "prowlarr_api_key", "integraciones",
        "Prowlarr · API key", kind="text", env="PROWLARR_API_KEY",
        secret=True,
        help="Se configura aquí o con PROWLARR_API_KEY.",
    ),
    Setting(
        "jackett_url", "jackett_url", "integraciones", "Jackett · URL",
        kind="url", env="JACKETT_URL",
        default="http://127.0.0.1:9117/api/v2.0",
    ),
    Setting(
        "jackett_api_key", "jackett_api_key", "integraciones",
        "Jackett · API key", kind="text", env="JACKETT_API_KEY",
        secret=True,
        help="Si está vacía, se usa la configuración local de Jackett.",
    ),
    Setting(
        "qbittorrent_url", "qbittorrent_url", "integraciones",
        "qBittorrent · URL", kind="url", env="QBIT_URL",
        default="http://127.0.0.1:1340",
    ),
    Setting(
        "qbittorrent_user", "qbittorrent_user", "integraciones",
        "qBittorrent · usuario", kind="text", env="QBIT_USER",
        default="qbittorrent",
    ),
    Setting(
        "qbittorrent_pass", "qbittorrent_pass", "integraciones",
        "qBittorrent · contraseña", kind="text", env="QBIT_PASS",
        secret=True,
    ),
    Setting(
        "jellyfin_url", "jellyfin_url", "integraciones", "Jellyfin · URL",
        kind="url", env="JELLYFIN_URL",
        default="http://127.0.0.1:8086",
    ),
    Setting(
        "jellyfin_api_key", "jellyfin_api_key", "integraciones",
        "Jellyfin · API key", kind="text", env="JELLYFIN_API_KEY",
        secret=True,
    ),
    Setting(
        "radarr_url", "radarr_url", "integraciones", "Radarr · URL",
        kind="url", env="RADARR_URL",
        default="http://127.0.0.1:7878/api/v3",
        help="Solo lectura: biblioteca y cola.",
    ),
    Setting(
        "radarr_api_key", "radarr_api_key", "integraciones",
        "Radarr · API key", kind="text", env="RADARR_API_KEY", secret=True,
    ),
    Setting(
        "sonarr_url", "sonarr_url", "integraciones", "Sonarr · URL",
        kind="url", env="SONARR_URL",
        default="http://127.0.0.1:8989/api/v3",
        help="Solo lectura: biblioteca y cola.",
    ),
    Setting(
        "sonarr_api_key", "sonarr_api_key", "integraciones",
        "Sonarr · API key", kind="text", env="SONARR_API_KEY", secret=True,
    ),
    Setting(
        "tmdb_api_key", "tmdb_api_key", "integraciones", "TMDB · API key",
        kind="text", env="TMDB_API_KEY", secret=True,
    ),
    # Directorios
    Setting(
        "catalog_library", "catalog_library", "directorios",
        "Biblioteca del catálogo", kind="path",
        env="CATALOGO_CATALOG_LIBRARY", default="/mnt/catalogo",
        help="Donde se escriben los .strm de TOD.",
    ),
    Setting(
        "library_root", "library_root", "directorios",
        "Biblioteca existente (Radarr/Sonarr)", kind="path",
        env="CATALOGO_LIBRARY_ROOT", default="/mnt/library",
    ),
    Setting(
        "seed_root", "seed_root", "directorios", "Raíz de semillas",
        kind="path", env="CATALOGO_SEED_ROOT",
        default="/mnt/descargas/catalogo",
    ),
    Setting(
        "downloads_root", "downloads_root", "directorios",
        "Descargas de qBittorrent", kind="path",
        env="CATALOGO_DOWNLOADS_ROOT", default="/mnt/descargas",
    ),
    Setting(
        "ondemand_save_path", "ondemand_save_path", "directorios",
        "Carpeta on-demand", kind="path", env="CATALOGO_ONDEMAND_SAVE",
        default="/mnt/descargas/ondemand",
        help="Si se deja vacía se usa Descargas/ondemand.",
    ),
    Setting(
        "strm_roots", "strm_roots", "directorios", "Raíces .strm",
        kind="paths", env="CATALOGO_STRM_ROOTS",
        default="/mnt/catalogo:/mnt/palantir",
        help="Rutas permitidas para /catalog/lookup, separadas por ':' o ','.",
    ),
    # Reproducción
    Setting(
        "ondemand_max_active", "max_active_ondemand", "reproduccion",
        "Descargas on-demand simultáneas", kind="int",
        env="CATALOGO_MAX_ACTIVE_ONDEMAND", default=4,
        minimum=1, maximum=10,
    ),
    Setting(
        "max_stream_gb", "max_stream_bytes", "reproduccion",
        "Tamaño máximo de stream (GB)", kind="float",
        env="CATALOGO_MAX_STREAM_GB", default=0,
        minimum=0, help="0 = sin límite.",
    ),
    Setting(
        "playback_base_url", "playback_base_url", "reproduccion",
        "URL base de reproducción", kind="url",
        env="CATALOGO_PLAYBACK_BASE_URL",
        default="http://127.0.0.1:8773",
        help="Base que se escribe en los .strm nuevos.",
        restart_required=True,
    ),
    # Seguridad
    Setting(
        "web_allowed_hosts", "web_allowed_hosts", "seguridad",
        "Hosts permitidos", kind="text",
        env="CATALOGO_WEB_ALLOWED_HOSTS", default="",
        help="IPs o nombres separados por coma, además de localhost.",
    ),
    Setting(
        "web_token", "web_token", "seguridad", "Token de acceso web",
        kind="text", env="CATALOGO_WEB_TOKEN", secret=True,
        help="Vacío = acceso abierto en LAN/WireGuard.",
    ),
)

BY_KEY: dict[str, Setting] = {item.key: item for item in SETTINGS}


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT
                (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )


def _read_overrides(db_path: str | Path) -> dict[str, str]:
    """Lee los overrides sin crear la base si todavía no existe."""
    path = Path(db_path).expanduser()
    if not path.exists():
        return {}
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5
        )
    except sqlite3.Error:
        return {}
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _split_roots(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        values = [str(item) for item in raw]
    else:
        text = str(raw or "").replace(",", os.pathsep)
        values = text.split(os.pathsep)
    roots: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"la ruta debe ser absoluta: {value}")
        roots.append(str(path))
    if not roots:
        raise ValueError("indica al menos una ruta")
    return tuple(roots)


def _as_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def coerce(setting: Setting, raw: Any) -> Any:
    """Convierte y valida un valor para usarlo en `Config`."""
    if setting.kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{setting.label}: debe ser un entero") from exc
        if setting.minimum is not None and value < setting.minimum:
            raise ValueError(
                f"{setting.label}: mínimo {setting.minimum:g}"
            )
        if setting.maximum is not None and value > setting.maximum:
            raise ValueError(
                f"{setting.label}: máximo {setting.maximum:g}"
            )
        return value
    if setting.kind == "float":
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{setting.label}: debe ser un número") from exc
        if setting.minimum is not None and value < setting.minimum:
            raise ValueError(
                f"{setting.label}: mínimo {setting.minimum:g}"
            )
        if setting.maximum is not None and value > setting.maximum:
            raise ValueError(
                f"{setting.label}: máximo {setting.maximum:g}"
            )
        return value
    if setting.kind == "bool":
        return _as_bool(raw)
    if setting.kind == "path":
        text = str(raw or "").strip()
        if not text:
            raise ValueError(f"{setting.label}: ruta vacía")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{setting.label}: la ruta debe ser absoluta")
        return path
    if setting.kind == "paths":
        return _split_roots(raw)
    if setting.kind == "url":
        text = str(raw or "").strip()
        parsed = urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"{setting.label}: URL http(s) válida, por ejemplo "
                "http://127.0.0.1:9696"
            )
        return text.rstrip("/")
    if setting.kind == "choice":
        text = str(raw or "").strip()
        if setting.choices and text not in setting.choices:
            raise ValueError(
                f"{setting.label}: valores válidos {', '.join(setting.choices)}"
            )
        return text
    return str(raw or "").strip()


def _storage_value(setting: Setting, value: Any) -> str:
    if setting.kind == "paths":
        return os.pathsep.join(value)
    if setting.kind in ("path",):
        return str(value)
    if setting.kind == "bool":
        return "1" if value else "0"
    return str(value)


def apply_setting(config: Config, setting: Setting, value: Any) -> Config:
    if setting.key == "ondemand_max_active":
        return replace(config, max_active_ondemand=int(value))
    if setting.key == "max_stream_gb":
        return replace(config, max_stream_bytes=int(float(value) * 1024**3))
    if setting.kind == "paths":
        return replace(config, **{setting.attr: tuple(value)})
    if setting.kind == "path":
        return replace(config, **{setting.attr: Path(value)})
    return replace(config, **{setting.attr: value})


def load_config(base: Config) -> Config:
    """Aplica los overrides de la BD sobre una configuración base."""
    overrides = _read_overrides(base.db_path)
    if not overrides:
        return base
    config = base
    applied: set[str] = set()
    for setting in SETTINGS:
        if setting.key not in overrides:
            continue
        try:
            value = coerce(setting, overrides[setting.key])
        except ValueError:
            # Un valor corrupto no debe impedir arrancar: se ignora y se
            # mantiene el valor del entorno.
            continue
        config = apply_setting(config, setting, value)
        applied.add(setting.key)
    if "downloads_root" in applied and "ondemand_save_path" not in applied:
        config = replace(
            config, ondemand_save_path=config.downloads_root / "ondemand"
        )
    return config


def _source(setting: Setting, config: Config, overrides: dict[str, str]) -> str:
    if setting.key in overrides:
        return "db"
    current = getattr(config, setting.attr, None)
    if setting.secret:
        return "env" if current else "default"
    if setting.env:
        keys = [setting.env]
        if setting.env.startswith("CATALOGO_"):
            keys.insert(0, f"TOD_{setting.env[len('CATALOGO_'):]}")
        elif setting.env.startswith("TOD_"):
            keys.append(f"CATALOGO_{setting.env[len('TOD_'):]}")
        if any(key in os.environ for key in keys):
            return "env"
    return "default"


def _hint(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "••••"
    return f"••••{text[-4:]}"


def _display(config: Config, setting: Setting) -> Any:
    value = getattr(config, setting.attr, None)
    if setting.key == "max_stream_gb":
        return round(float(value or 0) / 1024**3, 3)
    if setting.key == "ondemand_max_active":
        return int(value or 0)
    if setting.kind == "paths":
        return os.pathsep.join(value or ())
    if setting.kind == "path":
        return str(value or "")
    if value is None:
        return ""
    return value


def serialize(config: Config) -> dict[str, Any]:
    """Vista segura para la web: sin secretos en claro."""
    overrides = _read_overrides(config.db_path)
    items: list[dict[str, Any]] = []
    for setting in SETTINGS:
        source = _source(setting, config, overrides)
        item: dict[str, Any] = {
            "key": setting.key,
            "section": setting.section,
            "label": setting.label,
            "kind": setting.kind,
            "env": setting.env,
            "help": setting.help,
            "secret": setting.secret,
            "source": source,
            "restart_required": setting.restart_required,
        }
        if setting.minimum is not None:
            item["minimum"] = setting.minimum
        if setting.maximum is not None:
            item["maximum"] = setting.maximum
        if setting.secret:
            value = getattr(config, setting.attr, None)
            item["configured"] = bool(value)
            item["hint"] = _hint(value)
            item["value"] = ""
        else:
            item["value"] = _display(config, setting)
        items.append(item)
    sections = [
        {
            "id": section,
            "label": label,
            "items": [item for item in items if item["section"] == section],
        }
        for section, label in SECTION_LABELS.items()
    ]
    return {
        "sections": sections,
        "settings": items,
        "restart_required": [],
    }


def preview_config(
    base: Config, updates: dict[str, Any] | None = None
) -> Config:
    """Config con valores sin guardar, para probar una integración antes de guardar."""
    config = base
    for key, raw in (updates or {}).items():
        setting = BY_KEY.get(str(key))
        if setting is None:
            raise ValueError(f"ajuste desconocido: {key}")
        if raw is None or str(raw) == "":
            continue
        value = coerce(setting, raw)
        config = apply_setting(config, setting, value)
    return config


def save_settings(
    db_path: str | Path,
    updates: dict[str, Any],
    *,
    clear: Iterable[str] = (),
) -> dict[str, Any]:
    """Valida y guarda ajustes. Devuelve qué se guardó y qué se borró."""
    if not isinstance(updates, dict):
        raise ValueError("los ajustes deben ser un objeto")
    clear_keys = [str(key) for key in clear]
    for key in clear_keys:
        if key not in BY_KEY:
            raise ValueError(f"ajuste desconocido: {key}")

    saved: list[str] = []
    cleared: list[str] = []
    for key, raw in updates.items():
        setting = BY_KEY.get(str(key))
        if setting is None:
            raise ValueError(f"ajuste desconocido: {key}")
        if setting.secret and (raw is None or str(raw) == ""):
            # Un secreto vacío no borra el existente: hay que usar `clear`.
            continue
        if not setting.secret and (
            raw is None or (isinstance(raw, str) and not raw.strip())
        ):
            if key not in clear_keys:
                clear_keys.append(str(key))
            continue
        value = coerce(setting, raw)
        saved.append((setting.key, _storage_value(setting, value)))

    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_table(conn)
        with conn:
            for key in clear_keys:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            for key, value in saved:
                conn.execute(
                    """
                    INSERT INTO settings(key, value, updated_at)
                    VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value),
                )
    finally:
        conn.close()

    saved_keys = [key for key, _ in saved]
    cleared = [
        key
        for key in dict.fromkeys(clear_keys)
        if key not in saved_keys
    ]
    restart = [
        key
        for key in saved_keys + cleared
        if BY_KEY[key].restart_required
    ]
    return {
        "saved": saved_keys,
        "cleared": cleared,
        "restart_required": restart,
    }


def path_status(raw: str | Path) -> dict[str, Any]:
    """Comprueba existencia, escritura y espacio de un directorio."""
    path = Path(raw).expanduser()
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    writable = os.access(path, os.W_OK) if exists else False
    probe = path if exists else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    result: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "writable": writable,
        "checked_parent": str(probe),
    }
    try:
        usage = shutil.disk_usage(probe)
        result.update(
            {
                "free": usage.free,
                "total": usage.total,
                "used_pct": (
                    round((usage.used / usage.total) * 100, 1)
                    if usage.total
                    else 0
                ),
            }
        )
        if not exists:
            result["writable"] = os.access(probe, os.W_OK)
    except OSError as exc:
        result["error"] = str(exc)[:200]
    return result
