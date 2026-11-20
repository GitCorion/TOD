"""Configuración de la construcción en la sombra.

Nada de aquí arranca servicios ni habla con qBittorrent/Prowlarr.
Las rutas y credenciales se pueden sobrescribir por entorno.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def _secret(name: str) -> str | None:
    """Lee un secreto del entorno o de .env.

    El .env de este proyecto puede contener una única clave en crudo; en ese
    caso se interpreta como RD_API_KEY.
    """
    value = os.environ.get(name)
    if value:
        return value.strip()
    path = BASE_DIR / ".env"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    if name == "RD_API_KEY" and text and "=" not in text:
        return text.strip()
    return None


def _jackett_key() -> str | None:
    """JACKETT_API_KEY o la clave del ServerConfig.json local."""
    value = os.environ.get("JACKETT_API_KEY")
    if value:
        return value.strip()
    path = Path(
        os.environ.get(
            "JACKETT_CONFIG",
            str(Path.home() / ".config" / "Jackett" / "ServerConfig.json"),
        )
    ).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = payload.get("APIKey")
    return key.strip() if isinstance(key, str) and key.strip() else None


def _roots_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    parts = [
        item.strip()
        for item in raw.replace(",", os.pathsep).split(os.pathsep)
        if item.strip()
    ]
    return tuple(parts) or default


@dataclass(frozen=True)
class Config:
    db_path: Path
    legacy_db: Path
    library_root: Path
    seed_root: Path
    migrations_dir: Path
    prowlarr_url: str
    prowlarr_api_key: str | None
    qbittorrent_url: str
    qbittorrent_user: str
    qbittorrent_pass: str | None
    jackett_url: str
    jackett_api_key: str | None
    rd_api_key: str | None
    max_stream_bytes: int
    tmdb_api_key: str | None = None
    catalog_library: Path = Path("/mnt/catalogo")
    max_active_ondemand: int = 4
    radarr_url: str = "http://127.0.0.1:7878/api/v3"
    radarr_api_key: str | None = None
    sonarr_url: str = "http://127.0.0.1:8989/api/v3"
    sonarr_api_key: str | None = None
    web_token: str | None = None
    jellyfin_url: str = "http://127.0.0.1:8086"
    jellyfin_api_key: str | None = None
    web_allowed_hosts: str = ""
    downloads_root: Path = Path("/mnt/descargas")
    ondemand_save_path: Path = Path("/mnt/descargas/ondemand")
    strm_roots: tuple[str, ...] = ("/mnt/catalogo", "/mnt/palantir")
    playback_base_url: str = "http://127.0.0.1:8773"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            db_path=_path("CATALOGO_DB", BASE_DIR / "var" / "catalogo.db"),
            legacy_db=_path(
                "CATALOGO_LEGACY_DB", BASE_DIR / "assets.db"
            ),
            library_root=_path("CATALOGO_LIBRARY_ROOT", Path("/mnt/library")),
            seed_root=_path(
                "CATALOGO_SEED_ROOT", Path("/mnt/descargas/catalogo")
            ),
            migrations_dir=_path(
                "CATALOGO_MIGRATIONS", BASE_DIR / "migrations"
            ),
            prowlarr_url=os.environ.get(
                "PROWLARR_URL", "http://127.0.0.1:9696/api/v1"
            ),
            prowlarr_api_key=os.environ.get("PROWLARR_API_KEY") or None,
            qbittorrent_url=os.environ.get(
                "QBIT_URL", "http://127.0.0.1:1340"
            ),
            qbittorrent_user=os.environ.get("QBIT_USER", "qbittorrent"),
            qbittorrent_pass=os.environ.get("QBIT_PASS") or None,
            jackett_url=os.environ.get(
                "JACKETT_URL", "http://127.0.0.1:9117/api/v2.0"
            ),
            jackett_api_key=os.environ.get("JACKETT_API_KEY") or _jackett_key(),
            rd_api_key=_secret("RD_API_KEY"),
            max_stream_bytes=int(
                float(os.environ.get("CATALOGO_MAX_STREAM_GB", "0")) * 1024**3
            ),
            tmdb_api_key=_secret("TMDB_API_KEY"),
            catalog_library=_path(
                "CATALOGO_CATALOG_LIBRARY", Path("/mnt/catalogo")
            ),
            max_active_ondemand=int(
                os.environ.get("CATALOGO_MAX_ACTIVE_ONDEMAND", "4")
            ),
            radarr_url=os.environ.get(
                "RADARR_URL", "http://127.0.0.1:7878/api/v3"
            ),
            radarr_api_key=_secret("RADARR_API_KEY"),
            sonarr_url=os.environ.get(
                "SONARR_URL", "http://127.0.0.1:8989/api/v3"
            ),
            sonarr_api_key=_secret("SONARR_API_KEY"),
            web_token=os.environ.get("CATALOGO_WEB_TOKEN")
            or _secret("CATALOGO_WEB_TOKEN"),
            jellyfin_url=os.environ.get(
                "JELLYFIN_URL", "http://127.0.0.1:8086"
            ),
            jellyfin_api_key=_secret("JELLYFIN_API_KEY"),
            web_allowed_hosts=os.environ.get(
                "CATALOGO_WEB_ALLOWED_HOSTS", ""
            ),
            downloads_root=_path(
                "CATALOGO_DOWNLOADS_ROOT", Path("/mnt/descargas")
            ),
            ondemand_save_path=_path(
                "CATALOGO_ONDEMAND_SAVE", Path("/mnt/descargas/ondemand")
            ),
            strm_roots=_roots_from_env(
                "CATALOGO_STRM_ROOTS", ("/mnt/catalogo", "/mnt/palantir")
            ),
            playback_base_url=os.environ.get(
                "CATALOGO_PLAYBACK_BASE_URL", "http://127.0.0.1:8773"
            ),
        )

    @classmethod
    def load(cls, db_path: str | Path | None = None) -> "Config":
        """Configuración efectiva: entorno + overrides guardados en la web.

        La base de datos manda sobre el entorno, pero el entorno sigue siendo
        el valor de arranque cuando todavía no hay nada configurado.
        """
        base = cls.from_env()
        if db_path is not None:
            base = replace(base, db_path=Path(db_path).expanduser())
        from .app_settings import load_config

        return load_config(base)

    def content_roots(self) -> list[str]:
        """Raíces donde buscar ficheros de torrents, según la configuración."""
        values = (
            self.ondemand_save_path,
            self.downloads_root,
            self.catalog_library,
            self.library_root,
            self.seed_root,
        )
        return list(dict.fromkeys(str(value) for value in values))

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrations_dir.mkdir(parents=True, exist_ok=True)


def media_type(path: str) -> str:
    return {
        ".mkv": "video/x-matroska",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".avi": "video/x-msvideo",
        ".ts": "video/mp2t",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }.get(Path(path).suffix.lower(), "application/octet-stream")
