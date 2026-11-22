"""Qué necesita atención en TOD, en una sola lista.

La lista se construye con datos que ya existen: configuración, rutas,
descargas, trabajos y el último estado guardado de cada integración. No hace
comprobaciones de red al leerla; de eso se encarga la tarea `attention-check`,
que se ejecuta en segundo plano y deja el resultado en `integration_health`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import integrations, jobs
from .app_settings import path_status
from .config import Config
from .db import connect
from .scheduler import list_tasks

SEVERITY_ORDER = {"fail": 0, "warn": 1, "info": 2}
GROUP_ORDER = {
    "configuracion": 0,
    "integraciones": 1,
    "directorios": 2,
    "descargas": 3,
    "tareas": 4,
    "sistema": 5,
}

DISK_WARN_BYTES = 1024**3
DISK_FAIL_BYTES = 256 * 1024**2
FAILURES_BEFORE_ALERT = 2
STALE_TASK = timedelta(minutes=15)
STALE_CHECK = timedelta(minutes=20)
CHECK_TIMEOUT = 20.0

# Servicio -> etiqueta, atributo de configuración y gravedad si falla.
SERVICES: dict[str, dict[str, Any]] = {
    "qbittorrent": {
        "label": "qBittorrent",
        "attr": "qbittorrent_pass",
        "severity": "fail",
        "focus": "qbittorrent_pass",
    },
    "prowlarr": {
        "label": "Prowlarr",
        "attr": "prowlarr_api_key",
        "severity": "fail",
        "focus": "prowlarr_api_key",
    },
    "jackett": {
        "label": "Jackett",
        "attr": "jackett_api_key",
        "severity": "warn",
        "focus": "jackett_api_key",
    },
    "jellyfin": {
        "label": "Jellyfin",
        "attr": "jellyfin_api_key",
        "severity": "warn",
        "focus": "jellyfin_api_key",
    },
    "radarr": {
        "label": "Radarr",
        "attr": "radarr_api_key",
        "severity": "warn",
        "focus": "radarr_api_key",
    },
    "sonarr": {
        "label": "Sonarr",
        "attr": "sonarr_api_key",
        "severity": "warn",
        "focus": "sonarr_api_key",
    },
    "tmdb": {
        "label": "TMDB",
        "attr": "tmdb_api_key",
        "severity": "warn",
        "focus": "tmdb_api_key",
    },
    "realdebrid": {
        "label": "Real-Debrid",
        "attr": "rd_api_key",
        "severity": "warn",
        "focus": "rd_api_key",
    },
}

# Rutas que se comprueban siempre. `fail` significa que sin ellas no se puede
# completar el flujo principal; `warn`, que conviene revisarlas.
PATHS: tuple[tuple[str, str, str], ...] = (
    ("catalog_library", "Carpeta de los .strm", "fail"),
    ("downloads_root", "Descargas de qBittorrent", "fail"),
    ("ondemand_save_path", "Carpeta on-demand", "fail"),
    ("seed_root", "Raíz de semillas", "warn"),
)


@dataclass(frozen=True)
class Item:
    key: str
    severity: str
    title: str
    detail: str = ""
    hint: str = ""
    href: str = ""
    group: str = "sistema"
    since: str | None = None
    action: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _configured(config: Config, name: str) -> bool:
    attr = SERVICES[name]["attr"]
    return bool(getattr(config, attr, None))


def _configured_services(config: Config) -> list[str]:
    return [name for name in SERVICES if _configured(config, name)]


def _source_severity(config: Config, name: str) -> str:
    """Prowlarr o Jackett: falla el conjunto, no uno de los dos."""
    if name not in ("prowlarr", "jackett"):
        return str(SERVICES[name]["severity"])
    other = "jackett" if name == "prowlarr" else "prowlarr"
    if _configured(config, other):
        return "warn"
    return "fail"


def _setup(config: Config) -> list[Item]:
    missing: list[str] = []
    if not getattr(config, "qbittorrent_pass", None):
        missing.append("qBittorrent")
    if not (
        getattr(config, "prowlarr_api_key", None)
        or getattr(config, "jackett_api_key", None)
    ):
        missing.append("Prowlarr o Jackett")
    if not getattr(config, "jellyfin_api_key", None):
        missing.append("Jellyfin")
    if not getattr(config, "tmdb_api_key", None):
        missing.append("TMDB")
    if not missing:
        return []
    return [
        Item(
            key="setup",
            severity="fail",
            title="Falta configuración inicial",
            detail="Falta: " + ", ".join(missing) + ". Sin ello la búsqueda o la reproducción pueden fallar.",
            hint="Completa las integraciones en Configuración.",
            href="#/settings?section=integraciones",
            group="integraciones",
            action="Configurar",
        )
    ]


def _paths(config: Config) -> list[Item]:
    items: list[Item] = []
    seen: dict[str, str] = {}
    paths = list(PATHS)
    if config.library_root_movies or config.library_root_series:
        if config.library_root_movies:
            paths.append(
                ("library_root_movies", "Carpeta de películas", "warn")
            )
        if config.library_root_series:
            paths.append(
                ("library_root_series", "Carpeta de series", "warn")
            )
    elif config.radarr_api_key or config.sonarr_api_key:
        paths.append(("library_root", "Carpeta de biblioteca", "warn"))
    for attr, label, severity in paths:
        raw = getattr(config, attr, None)
        if not raw:
            continue
        path = str(raw)
        if path in seen:
            continue
        seen[path] = label
        try:
            state = path_status(path)
        except Exception as exc:  # una ruta rota no debe tumbar Inicio
            items.append(
                Item(
                    key=f"path:{path}",
                    severity=severity,
                    title=f"No se puede comprobar {label}",
                    detail=str(exc)[:200],
                    hint="Revisa que la ruta exista y sea accesible.",
                    href="#/settings?section=directorios",
                    group="directorios",
                    action="Revisar",
                )
            )
            continue
        if not state.get("exists"):
            # Las carpetas que TOD crea al vuelo no son un aviso si el padre
            # existe y es escribible. Las dos raíces principales sí deben estar.
            required = attr in ("catalog_library", "downloads_root")
            if required or not state.get("writable"):
                items.append(
                    Item(
                        key=f"path:{path}",
                        severity=severity,
                        title=f"{label} no existe",
                        detail=f"La ruta {path} no está disponible.",
                        hint="Crea el directorio o corrige la ruta.",
                        href="#/settings?section=directorios",
                        group="directorios",
                        action="Revisar ruta",
                    )
                )
            continue
        if not state.get("writable"):
            items.append(
                Item(
                    key=f"path:{path}",
                    severity="fail",
                    title=f"{label} no es escribible",
                    detail=f"TOD no puede escribir en {path}.",
                    hint="Revisa permisos y el usuario del servicio.",
                    href="#/settings?section=directorios",
                    group="directorios",
                    action="Revisar permisos",
                )
            )
        free = state.get("free")
        if isinstance(free, (int, float)):
            if free < DISK_FAIL_BYTES:
                items.append(
                    Item(
                        key=f"disk:{path}",
                        severity="fail",
                        title=f"Sin espacio en {label}",
                        detail=f"Quedan menos de 256 MB libres en {path}.",
                        hint="Libera espacio antes de seguir descargando.",
                        href="#/settings?section=directorios",
                        group="directorios",
                        action="Ver espacio",
                    )
                )
            elif free < DISK_WARN_BYTES:
                items.append(
                    Item(
                        key=f"disk:{path}",
                        severity="warn",
                        title=f"Poco espacio en {label}",
                        detail=f"Quedan menos de 1 GB libres en {path}.",
                        hint="Libera espacio o amplía el disco.",
                        href="#/settings?section=directorios",
                        group="directorios",
                        action="Ver espacio",
                    )
                )
    return items


def _downloads(db_path: str | Path) -> list[Item]:
    try:
        with connect(db_path) as conn:
            paused = int(
                conn.execute(
                    "SELECT COUNT(*) FROM torrents WHERE state='paused'"
                ).fetchone()[0]
            )
            failed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM torrents WHERE state='error'"
                ).fetchone()[0]
            )
    except Exception:
        return []
    items: list[Item] = []
    if paused:
        items.append(
            Item(
                key="downloads:paused",
                severity="warn",
                title=f"{paused} descarga{'s' if paused != 1 else ''} pausada{'s' if paused != 1 else ''}",
                detail="Hay torrents en pausa que pueden necesitar atención.",
                hint="Revisa si deben reanudarse o abandonarse.",
                href="#/downloads?state=pausado",
                group="descargas",
                action="Ver pausadas",
            )
        )
    if failed:
        items.append(
            Item(
                key="downloads:error",
                severity="warn",
                title=f"{failed} descarga{'s' if failed != 1 else ''} con error",
                detail="qBittorrent no puede completar esos torrents.",
                hint="Abre Descargas, revisa el estado y reintenta o borra.",
                href="#/downloads?state=error",
                group="descargas",
                action="Ver errores",
            )
        )
    return items


def _job_failures(db_path: str | Path, now: datetime) -> list[Item]:
    try:
        recent = jobs.list_jobs(db_path, limit=60)
    except Exception:
        return []
    cutoff = now - timedelta(days=1)
    failed = []
    for job in recent:
        if job.get("state") != "error" or job.get("kind") == "attention-check":
            continue
        if (_parse(job.get("finished_at") or job.get("created_at")) or now) < cutoff:
            continue
        text = str(job.get("error") or job.get("message") or "").lower()
        # Los fallos por falta de configuración ya aparecen en el aviso de setup.
        if "sin api key" in text or "sin contraseña" in text or "sin password" in text:
            continue
        failed.append(job)
    if not failed:
        return []
    latest = failed[0]
    raw = str(latest.get("error") or latest.get("message") or "sin detalle")
    lines = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("Traceback")
    ]
    detail = (lines[0] if lines else "sin detalle")[:200]
    return [
        Item(
            key="jobs:error",
            severity="warn",
            title=f"{len(failed)} tarea{'s' if len(failed) != 1 else ''} fallida{'s' if len(failed) != 1 else ''} en 24 h",
            detail=f"La última fue «{latest.get('kind')}»: {detail}",
            hint="Abre Tareas para ver el detalle y relanzarla.",
            href="#/jobs",
            group="tareas",
            action="Ver tareas",
        )
    ]


def _scheduler(db_path: str | Path, now: datetime) -> list[Item]:
    try:
        tasks = list_tasks(db_path, now=now)
    except Exception:
        return []
    overdue = []
    for task in tasks:
        if not task.get("enabled") or task.get("name") == "attention-check":
            continue
        nxt = _parse(task.get("next_run"))
        if nxt is not None and nxt < now - STALE_TASK:
            overdue.append(str(task.get("label") or task.get("name")))
    items: list[Item] = []
    if overdue:
        items.append(
            Item(
                key="scheduler:overdue",
                severity="warn",
                title="El scheduler está atrasado",
                detail="Tareas vencidas: " + ", ".join(overdue[:4]) + ".",
                hint="Revisa que TOD siga ejecutándose y abre Tareas programadas.",
                href="#/settings?section=tareas",
                group="tareas",
                action="Ver tareas",
            )
        )
    check = next(
        (task for task in tasks if task.get("name") == "attention-check"), None
    )
    last = (check or {}).get("last") or {}
    if last.get("state") == "error":
        items.append(
            Item(
                key="scheduler:check",
                severity="warn",
                title="La comprobación de estado falla",
                detail=str(last.get("error") or "sin detalle")[:200],
                hint="Pulsa «Comprobar ahora»; si sigue, revisa las tareas.",
                href="#/settings?section=tareas",
                group="tareas",
                action="Ver tarea",
            )
        )
    return items


def _integrations(db_path: str | Path, config: Config) -> list[Item]:
    try:
        with connect(db_path) as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM integration_health ORDER BY service"
                )
            ]
    except Exception:
        return []
    items: list[Item] = []
    newest: datetime | None = None
    for row in rows:
        checked = _parse(row.get("checked_at"))
        if checked is not None and (newest is None or checked > newest):
            newest = checked
        if row.get("ok"):
            continue
        if int(row.get("failures") or 0) < FAILURES_BEFORE_ALERT:
            continue
        name = str(row.get("service") or "")
        meta = SERVICES.get(name)
        if meta is None:
            continue
        severity = _source_severity(config, name) if name in ("prowlarr", "jackett") else str(meta["severity"])
        since = row.get("since") or row.get("checked_at")
        items.append(
            Item(
                key=f"integration:{name}",
                severity=severity,
                title=f"{meta['label']} no responde",
                detail=str(row.get("error") or "La última comprobación falló.")[:240],
                hint=str(
                    row.get("hint")
                    or "Revisa la URL, la clave y que el servicio esté activo."
                )[:240],
                href=(
                    "#/settings?section=integraciones"
                    f"&focus={meta['focus']}"
                ),
                group="integraciones",
                since=str(since) if since else None,
                action="Revisar",
            )
        )
    if (
        newest is not None
        and _now() - newest > STALE_CHECK
        and any(_configured(config, name) for name in SERVICES)
    ):
        items.append(
            Item(
                key="integration:stale",
                severity="info",
                title="La comprobación de integraciones está atrasada",
                detail="No se actualiza el estado desde hace más de 20 minutos.",
                hint="Pulsa «Comprobar ahora» en Estado.",
                href="#/system",
                group="integraciones",
                action="Comprobar",
            )
        )
    return items


def _sort(items: Iterable[Item]) -> list[Item]:
    return sorted(
        items,
        key=lambda item: (
            SEVERITY_ORDER.get(item.severity, 9),
            GROUP_ORDER.get(item.group, 9),
            item.title.lower(),
        ),
    )


def collect(
    db_path: str | Path,
    config: Config,
    *,
    now: datetime | None = None,
) -> list[Item]:
    """Lista ordenada de avisos, sin hacer red."""
    current = now or _now()
    items: list[Item] = []
    items.extend(_setup(config))
    items.extend(_paths(config))
    items.extend(_downloads(db_path))
    items.extend(_job_failures(db_path, current))
    items.extend(_scheduler(db_path, current))
    items.extend(_integrations(db_path, config))
    return _sort(items)


def counts(items: Iterable[Item]) -> dict[str, int]:
    result = {"fail": 0, "warn": 0, "info": 0, "total": 0}
    for item in items:
        if item.severity in result:
            result[item.severity] += 1
        result["total"] += 1
    return result


def status(
    db_path: str | Path,
    config: Config,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    items = collect(db_path, config, now=now)
    return {
        "items": [asdict(item) for item in items],
        "counts": counts(items),
        "generated_at": _iso(now),
    }


def record(
    db_path: str | Path,
    name: str,
    result: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Guarda el resultado de una integración y mantiene el contador de fallos."""
    key = integrations.ALIASES.get(name, name)
    if key not in SERVICES:
        return
    current = now or _now()
    checked = _iso(current)
    ok = bool(result.get("ok"))
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT failures, since FROM integration_health WHERE service = ?",
                (key,),
            ).fetchone()
            failures = 0 if ok else int((row["failures"] if row else 0) or 0) + 1
            since = None if ok else ((row["since"] if row else None) or checked)
            conn.execute(
                """
                INSERT INTO integration_health(
                    service, ok, checked_at, latency_ms, detail, error, hint,
                    failures, since
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service) DO UPDATE SET
                    ok = excluded.ok,
                    checked_at = excluded.checked_at,
                    latency_ms = excluded.latency_ms,
                    detail = excluded.detail,
                    error = excluded.error,
                    hint = excluded.hint,
                    failures = excluded.failures,
                    since = excluded.since
                """,
                (
                    key,
                    int(ok),
                    checked,
                    result.get("latency_ms"),
                    str(result.get("detail") or "")[:300] or None,
                    str(result.get("error") or "")[:300] or None,
                    str(result.get("hint") or "")[:300] or None,
                    failures,
                    since,
                ),
            )
            conn.commit()
    except Exception:
        # El estado es informativo: no debe romper la tarea ni la web.
        return


def _prune(db_path: str | Path, configured: Iterable[str]) -> None:
    names = list(configured)
    try:
        with connect(db_path) as conn:
            if names:
                placeholders = ",".join("?" for _ in names)
                conn.execute(
                    f"DELETE FROM integration_health WHERE service NOT IN ({placeholders})",
                    names,
                )
            else:
                conn.execute("DELETE FROM integration_health")
            conn.commit()
    except Exception:
        return


def run_checks(
    db_path: str | Path,
    config: Config,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Comprueba las integraciones configuradas en paralelo y guarda el estado."""
    configured = _configured_services(config)
    _prune(db_path, configured)
    if not configured:
        return {"checked": 0, "failing": 0, "services": [], "checked_at": _iso(now)}

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            name: pool.submit(
                integrations.test_integration, name, config, light=True
            )
            for name in configured
        }
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=CHECK_TIMEOUT)
            except FutureTimeout:
                results[name] = {
                    "ok": False,
                    "error": f"sin respuesta en {int(CHECK_TIMEOUT)} s",
                    "kind": "timeout",
                    "hint": integrations.NETWORK_HINTS["timeout"],
                }
            except Exception as exc:
                kind, hint = integrations.classify_error(exc)
                results[name] = {
                    "ok": False,
                    "error": str(exc)[:300],
                    "kind": kind,
                    "hint": hint,
                }

    for name, result in results.items():
        record(db_path, name, result, now=now)

    failing = sum(
        1
        for name, result in results.items()
        if not result.get("ok")
        and name in SERVICES
        and _source_severity(config, name) == "fail"
    )
    return {
        "checked": len(results),
        "failing": failing,
        "services": sorted(results),
        "checked_at": _iso(now),
    }
