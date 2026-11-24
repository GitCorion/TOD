"""Configuración de TOD.

Nada de aquí arranca servicios ni habla con qBittorrent/Prowlarr.
Las rutas y credenciales se pueden sobrescribir por entorno.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_value(name: str, default: str | None = None) -> str | None:
    """Lee `TOD_*` y mantiene compatibilidad con `CATALOGO_*`."""
    keys = [name]
    if name.startswith("CATALOGO_"):
        keys.insert(0, f"TOD_{name[len('CATALOGO_'):]}")
    for key in keys:
        if key in os.environ:
            return os.environ[key]
    return default


def _path(name: str, default: Path) -> Path:
    return Path(_env_value(name, str(default)) or str(default)).expanduser()


def _optional_path(name: str) -> Path | None:
    raw = _env_value(name)
    return Path(raw).expanduser() if raw else None


def _secret(name: str) -> str | None:
    """Lee un secreto del entorno o de .env (siempre como KEY=VALUE)."""
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


def _default_db_path() -> Path:
    """Base en `var/tod.db` para un clon; datos de usuario si es un wheel."""
    repo = (BASE_DIR / "pyproject.toml").exists() or (BASE_DIR / ".git").exists()
    if repo:
        current = BASE_DIR / "var" / "tod.db"
        legacy = BASE_DIR / "var" / "catalogo.db"
        if not current.exists() and legacy.exists():
            return legacy
        return current
    data = Path(
        os.environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
        )
    ) / "tod"
    return data / "tod.db"


def _roots_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env_value(name, "") or ""
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
    max_stream_bytes: int
    tmdb_api_key: str | None = None
    catalog_library: Path = Path("/mnt/catalogo")
    max_active_ondemand: int = 4
    radarr_url: str = "http://127.0.0.1:7878/api/v3"
    radarr_api_key: str | None = None
    sonarr_url: str = "http://127.0.0.1:8989/api/v3"
    sonarr_api_key: str | None = None
    web_token: str | None = None
    jellyfin_url: str = "http://127.0.0.1:8096"
    jellyfin_api_key: str | None = None
    web_allowed_hosts: str = ""
    downloads_root: Path = Path("/mnt/descargas")
    ondemand_save_path: Path = Path("/mnt/descargas/ondemand")
    strm_roots: tuple[str, ...] = ("/mnt/catalogo",)
    playback_base_url: str = "http://127.0.0.1:8773"
    # Radarr y Sonarr suelen usar roots distintos. Si se configuran, mandan
    # sobre `library_root`; si no, se mantiene la raíz común histórica.
    library_root_movies: Path | None = None
    library_root_series: Path | None = None
    move_target: Path | None = None

    @classmethod
    def from_env(cls) -> "Config":
        db_env = os.environ.get("TOD_DB") or os.environ.get("CATALOGO_DB")
        db_path = (
            Path(db_env).expanduser() if db_env else _default_db_path()
        )
        catalog_library = _path(
            "CATALOGO_CATALOG_LIBRARY", Path("/mnt/catalogo")
        )
        downloads_root = _path(
            "CATALOGO_DOWNLOADS_ROOT", Path("/mnt/descargas")
        )
        return cls(
            db_path=db_path,
            legacy_db=_path(
                "CATALOGO_LEGACY_DB", BASE_DIR / "assets.db"
            ),
            library_root=_path("CATALOGO_LIBRARY_ROOT", Path("/mnt/library")),
            library_root_movies=_optional_path(
                "CATALOGO_LIBRARY_ROOT_MOVIES"
            ),
            library_root_series=_optional_path(
                "CATALOGO_LIBRARY_ROOT_SERIES"
            ),
            move_target=_optional_path("CATALOGO_MOVE_TARGET"),
            seed_root=_path(
                "CATALOGO_SEED_ROOT", downloads_root / "catalogo"
            ),
            migrations_dir=_path(
                "CATALOGO_MIGRATIONS",
                Path(__file__).resolve().parent / "migrations",
            ),
            prowlarr_url=os.environ.get(
                "PROWLARR_URL", "http://127.0.0.1:9696/api/v1"
            ),
            prowlarr_api_key=os.environ.get("PROWLARR_API_KEY") or None,
            qbittorrent_url=os.environ.get(
                "QBIT_URL", "http://127.0.0.1:8080"
            ),
            qbittorrent_user=os.environ.get("QBIT_USER", "qbittorrent"),
            qbittorrent_pass=os.environ.get("QBIT_PASS") or None,
            jackett_url=os.environ.get(
                "JACKETT_URL", "http://127.0.0.1:9117/api/v2.0"
            ),
            jackett_api_key=os.environ.get("JACKETT_API_KEY") or _jackett_key(),
            max_stream_bytes=int(
                float(_env_value("CATALOGO_MAX_STREAM_GB", "0") or "0")
                * 1024**3
            ),
            tmdb_api_key=_secret("TMDB_API_KEY"),
            catalog_library=catalog_library,
            max_active_ondemand=int(
                _env_value("CATALOGO_MAX_ACTIVE_ONDEMAND", "4") or "4"
            ),
            radarr_url=os.environ.get(
                "RADARR_URL", "http://127.0.0.1:7878/api/v3"
            ),
            radarr_api_key=_secret("RADARR_API_KEY"),
            sonarr_url=os.environ.get(
                "SONARR_URL", "http://127.0.0.1:8989/api/v3"
            ),
            sonarr_api_key=_secret("SONARR_API_KEY"),
            web_token=_env_value("CATALOGO_WEB_TOKEN")
            or _secret("TOD_WEB_TOKEN")
            or _secret("CATALOGO_WEB_TOKEN"),
            jellyfin_url=os.environ.get(
                "JELLYFIN_URL", "http://127.0.0.1:8096"
            ),
            jellyfin_api_key=_secret("JELLYFIN_API_KEY"),
            web_allowed_hosts=_env_value(
                "CATALOGO_WEB_ALLOWED_HOSTS", ""
            ) or "",
            downloads_root=downloads_root,
            ondemand_save_path=_path(
                "CATALOGO_ONDEMAND_SAVE", Path("/mnt/descargas/ondemand")
            ),
            strm_roots=_roots_from_env(
                "CATALOGO_STRM_ROOTS", (str(catalog_library),)
            ),
            playback_base_url=_env_value(
                "CATALOGO_PLAYBACK_BASE_URL", "http://127.0.0.1:8773"
            ) or "http://127.0.0.1:8773",
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

    def effective_library_root(self) -> Path:
        """Carpeta padre de Películas y Series, calculada cuando se puede."""
        roots = [
            root
            for root in (self.library_root_movies, self.library_root_series)
            if root is not None
        ]
        if len(roots) >= 2:
            try:
                return Path(os.path.commonpath([str(root) for root in roots]))
            except ValueError:
                pass
        if roots:
            return roots[0].parent
        return self.library_root

    def library_roots(self) -> tuple[Path, ...]:
        """Raíces de la biblioteca existente: películas y series, o la común."""
        specific = (
            self.library_root_movies,
            self.library_root_series,
        )
        roots = tuple(root for root in specific if root is not None)
        return roots or (self.effective_library_root(),)

    def move_destination(self) -> Path:
        """Ruta donde se archivan los torrents on-demand completados."""
        return self.move_target or (self.effective_library_root() / "descargas")

    def archive_roots(self) -> tuple[Path, ...]:
        """Raíces donde qBittorrent puede tener ficheros ya archivados."""
        roots = list(self.library_roots())
        library_root = self.effective_library_root()
        if library_root not in roots:
            roots.append(library_root)
        if self.move_target and self.move_target not in roots:
            roots.append(self.move_target)
        return tuple(roots)

    def content_roots(self) -> list[str]:
        """Raíces donde buscar ficheros de torrents, según la configuración."""
        values = (
            self.ondemand_save_path,
            self.downloads_root,
            self.catalog_library,
            self.effective_library_root(),
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
