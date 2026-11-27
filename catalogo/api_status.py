"""Estado de la aplicación para la API.

Aquí vive lo que la web solo debe serializar: servicios, discos, indexers,
tareas y contadores. Sin acceso a `Handler` ni a la respuesta HTTP.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from . import app_settings, jobs, queries
from .config import Config
from .db import connect
from .scheduler import list_tasks

SERVICES = (
    "tod.service",
    "qbittorrent.service",
    "prowlarr.service",
    "jackett.service",
    "jellyfin.service",
)

_INDEXER_CACHE: dict[str, tuple[float, list[dict[str, Any]], str | None]] = {}
_INDEXER_TTL = 120.0


def _systemctl(unit: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        state = (result.stdout or result.stderr or "unknown").strip().splitlines()
        return {"unit": unit, "active": state[0] if state else "unknown"}
    except Exception as exc:
        return {"unit": unit, "active": "unknown", "error": str(exc)[:120]}


def _runtime() -> str:
    """Entorno de ejecución para no interpretar contenedores como servicios."""
    explicit = (os.environ.get("TOD_RUNTIME") or "").strip().lower()
    if explicit in ("docker", "systemd"):
        return explicit
    return "docker" if Path("/.dockerenv").exists() else "systemd"


def list_indexers(config: Config) -> tuple[list[dict[str, Any]], str | None]:
    """Indexers de Prowlarr con su estado, cacheado unos segundos."""
    indexers: list[dict[str, Any]] = []
    prowlarr_error = None
    cache_key = str(config.prowlarr_url)
    cached = _INDEXER_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _INDEXER_TTL:
        return cached[1], cached[2]
    try:
        from .prowlarr import ProwlarrClient

        client = ProwlarrClient.from_config(config)
        enabled = {
            int(row["id"]): bool(row.get("enable"))
            for row in client.indexers()
            if row.get("id") is not None
        }
        stats = client.indexer_stats()
        for row in stats.get("indexers") or []:
            failed = sum(
                int(row.get(key) or 0)
                for key in (
                    "numberOfFailedQueries",
                    "numberOfFailedGrabs",
                    "numberOfFailedRssQueries",
                    "numberOfFailedAuthQueries",
                )
            )
            indexer_id = int(row.get("indexerId") or 0)
            indexers.append(
                {
                    "id": indexer_id,
                    "name": row.get("indexerName"),
                    "enabled": enabled.get(indexer_id, True),
                    "queries": int(row.get("numberOfQueries") or 0),
                    "grabs": int(row.get("numberOfGrabs") or 0),
                    "rss_queries": int(row.get("numberOfRssQueries") or 0),
                    "failed": failed,
                    "response_ms": int(row.get("averageResponseTime") or 0),
                }
            )
        indexers.sort(key=lambda item: (-item["failed"], -item["response_ms"]))
    except Exception as exc:
        prowlarr_error = str(exc)[:200]
    _INDEXER_CACHE[cache_key] = (time.monotonic(), indexers, prowlarr_error)
    return indexers, prowlarr_error


def invalidate_indexers(config: Config) -> None:
    _INDEXER_CACHE.pop(str(config.prowlarr_url), None)


def monitor_health(db_path: str | Path, config: Config) -> dict[str, Any]:
    """Salud de RSS, barrido e indexers, para Seguimiento y Estado."""
    recent = jobs.list_jobs(db_path, limit=100)

    def latest(kind: str):
        return next((job for job in recent if job.get("kind") == kind), None)

    def brief(job):
        if not job:
            return None
        result = job.get("result") or {}
        keys = (
            "fetched",
            "inserted",
            "updated",
            "new_titles",
            "exported_keys",
            "unmonitored_keys",
            "checked",
            "found",
            "waiting",
            "errors",
        )
        return {
            "id": job.get("id"),
            "state": job.get("state"),
            "created_at": job.get("created_at"),
            "finished_at": job.get("finished_at"),
            "message": job.get("message"),
            "error": job.get("error"),
            "result": {key: result[key] for key in keys if key in result},
        }

    indexers, prowlarr_error = list_indexers(config)
    # El scheduler real es el interno (`jobs`), no los timers de systemd de
    # la instalación antigua.
    tasks = {task["name"]: task for task in list_tasks(db_path)}

    def timer(name: str) -> dict[str, Any]:
        task = tasks.get(name) or {}
        return {
            "active": "active" if task.get("enabled") else "inactive",
            "next_run": task.get("next_run"),
        }

    return {
        "timers": {
            "rss": timer("rss-sync"),
            "monitor": timer("monitor"),
        },
        "last_rss": brief(latest("rss-sync")),
        "last_monitor": brief(latest("monitor")),
        "indexers": indexers[:12],
        "prowlarr_error": prowlarr_error,
    }


def setup_status(config: Config, db_path: str | Path) -> dict[str, Any]:
    """Primeros pasos: qué falta para que TOD pueda funcionar.

    Una integración configurada pero con la última prueba fallida vuelve a
    considerarse pendiente. Si nunca se ha probado, no se castiga: las
    instalaciones existentes no se ven obligadas a repetir el asistente.
    """
    def path_ok(value) -> bool:
        if not value:
            return False
        data = app_settings.path_status(value)
        return bool(data.get("exists") and data.get("is_dir") and data.get("writable"))

    with closing(connect(db_path)) as conn:
        health = {
            row["service"]: dict(row)
            for row in conn.execute(
                "SELECT service, ok, checked_at, error, detail "
                "FROM integration_health"
            ).fetchall()
        }

    def state(service: str, configured: bool) -> dict[str, Any]:
        row = health.get(service)
        validated = None if row is None else bool(row.get("ok"))
        return {
            "configured": configured,
            "done": configured and validated is not False,
            "validated": validated,
            "checked_at": row.get("checked_at") if row else None,
            "error": row.get("error") if row else None,
            "detail": row.get("detail") if row else None,
        }

    paths = {
        "catalog_library": config.catalog_library,
        "downloads_root": config.downloads_root,
        "seed_root": config.seed_root,
    }
    directory_state = {
        key: {"value": str(value), "ok": path_ok(value)}
        for key, value in paths.items()
    }
    qbittorrent = state(
        "qbittorrent", bool(config.qbittorrent_url and config.qbittorrent_pass)
    )
    prowlarr = state("prowlarr", bool(config.prowlarr_api_key))
    jackett = state("jackett", bool(config.jackett_api_key))
    sources_configured = [item for item in (prowlarr, jackett) if item["configured"]]
    sources_done = any(item["done"] for item in sources_configured)
    sources_validated = None
    if any(item["validated"] is True for item in sources_configured):
        sources_validated = True
    elif sources_configured and all(
        item["validated"] is False for item in sources_configured
    ):
        sources_validated = False
    directories_configured = all(item["ok"] for item in directory_state.values())
    directories = state("directories", directories_configured)
    # Las rutas se comprueban en cada lectura, no dependen de una prueba de red.
    directories["validated"] = directories_configured
    directories["done"] = directories_configured
    jellyfin = state(
        "jellyfin", bool(config.jellyfin_url and config.jellyfin_api_key)
    )
    runtime = _runtime()
    suggestions = {}
    if runtime == "docker":
        suggestions = {
            "qbittorrent_url": "http://qbittorrent:8080",
            "qbittorrent_user": "admin",
            "prowlarr_url": "http://prowlarr:9696/api/v1",
            "jackett_url": "http://jackett:9117/api/v2.0",
            "jellyfin_url": "http://jellyfin:8096",
            "catalog_library": "/media/catalogo",
            "downloads_root": "/downloads",
            "seed_root": "/downloads/catalogo",
        }
    return {
        "runtime": runtime,
        "suggestions": suggestions,
        "needed": not (
            qbittorrent["done"] and sources_done and directories["done"]
        ),
        "steps": {
            "qbittorrent": {
                **qbittorrent,
                "url": str(config.qbittorrent_url or ""),
                "user": config.qbittorrent_user or "",
                "has_secret": bool(config.qbittorrent_pass),
            },
            "sources": {
                "configured": bool(sources_configured),
                "done": sources_done,
                "validated": sources_validated,
                "prowlarr": {
                    **prowlarr,
                    "url": str(config.prowlarr_url or ""),
                    "has_secret": bool(config.prowlarr_api_key),
                },
                "jackett": {
                    **jackett,
                    "url": str(config.jackett_url or ""),
                    "has_secret": bool(config.jackett_api_key),
                },
            },
            "directories": {
                **directories,
                "paths": directory_state,
            },
            "jellyfin": {
                **jellyfin,
                "url": str(config.jellyfin_url or ""),
                "has_secret": bool(config.jellyfin_api_key),
            },
        },
    }


def system_payload(
    *,
    config: Config,
    db_path: str | Path,
    qbit: Any,
    qbit_error: str | None,
    play_service: Any,
    play_error: str | None,
    token: str | None,
) -> dict[str, Any]:
    """Diagnóstico completo de `Sistema → Estado`."""
    db = Path(db_path)
    qbit_info: dict[str, Any] = {"ok": False, "error": qbit_error}
    if qbit is not None:
        try:
            torrents = qbit.torrents()
            qbit_info = {
                "ok": True,
                "version": qbit.app_version(),
                "torrents": len(torrents),
            }
        except Exception as exc:
            qbit_info = {"ok": False, "error": str(exc)[:200]}
    with closing(connect(db)) as conn:
        counts = {
            "media_items": conn.execute(
                "SELECT COUNT(*) FROM media_items"
            ).fetchone()[0],
            "releases": conn.execute(
                "SELECT COUNT(*) FROM releases"
            ).fetchone()[0],
            "torrents": conn.execute(
                "SELECT COUNT(*) FROM torrents"
            ).fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        }
    return {
        "runtime": _runtime(),
        "services": [_systemctl(unit) for unit in SERVICES],
        "disk": [
            queries._disk(path)
            for path in dict.fromkeys(
                str(path)
                for path in (
                    config.catalog_library,
                    config.seed_root,
                    config.effective_library_root(),
                )
            )
        ],
        "db": {
            "path": str(db),
            "size": db.stat().st_size if db.exists() else 0,
            **counts,
        },
        "qbit": qbit_info,
        "capabilities": {
            "qbittorrent": bool(config.qbittorrent_pass),
            "prowlarr": bool(config.prowlarr_api_key),
            "jackett": bool(config.jackett_api_key),
            "jellyfin": bool(config.jellyfin_api_key),
            "radarr": bool(config.radarr_api_key),
            "sonarr": bool(config.sonarr_api_key),
            "trailarr": bool(config.trailarr_api_key),
            "tmdb": bool(config.tmdb_api_key),
            "play_service": play_service is not None,
        },
        "play_error": play_error,
        "paths": {
            "catalog_library": str(config.catalog_library),
            "seed_root": str(config.seed_root),
            "library_root": str(config.effective_library_root()),
            "library_root_movies": (
                str(config.library_root_movies)
                if config.library_root_movies
                else ""
            ),
            "library_root_series": (
                str(config.library_root_series)
                if config.library_root_series
                else ""
            ),
            "downloads_root": str(config.downloads_root),
            "ondemand_save_path": str(config.ondemand_save_path),
            "move_target": str(config.move_destination()),
            "strm_roots": ":".join(config.strm_roots),
        },
        "auth_required": bool(token),
        "trackers": queries.list_trackers(db),
        "scheduler": list_tasks(db),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
