"""Panel web, API, reproducción y scheduler en un único proceso.

- API JSON + SPA sin dependencias externas.
- Las mutaciones exigen la cabecera `X-Catalogo: web` (anti-CSRF) y, si se
  configura `CATALOGO_WEB_TOKEN`, una sesión válida.
- Nunca expone `download_url`, claves de API ni enlaces de debrid.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import gzip
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import app_settings, admin, attention, integrations, jellyfin, jobs, ondemand, queries
from . import settings, strm_inventory
from . import strm as strm_module
from .arr_data import ArrData
from .config import Config
from .db import connect, init_db
from .http import CatalogHandler as PlaybackHandler
from .jobs import JobConflict, JobError
from .qbittorrent import QbittorrentClient, QbittorrentError
from .qbit_write import QbittorrentWriteClient
from .resolver import Resolver
from .retention import finish_intent
from .scheduler import Scheduler, list_tasks, save_task
from .status import asset_status
from .stream_guard import StreamGuard

STATIC_DIR = Path(__file__).resolve().parent / "web_static"
COOKIE = "catalogo_web"
COOKIE_MAX_AGE = 7 * 24 * 3600
MAX_BODY = 1_000_000
HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")

_INDEXER_CACHE: dict[str, tuple[float, list[dict[str, Any]], str | None]] = {}
_INDEXER_TTL = 120.0


INTERNAL_JOB_KINDS = ("attention-check",)

SERVICES = (
    "tod.service",
    "qbittorrent.service",
    "prowlarr.service",
    "jackett.service",
    "jellyfin.service",
)


class WebError(RuntimeError):
    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def _now() -> int:
    return int(time.time())


def _sign(token: str, expiry: int) -> str:
    return hmac.new(
        token.encode(), f"catalogo|{expiry}".encode(), hashlib.sha256
    ).hexdigest()


def _make_cookie(token: str) -> str:
    expiry = _now() + COOKIE_MAX_AGE
    return f"{expiry}.{_sign(token, expiry)}"


def _valid_cookie(token: str, value: str | None) -> bool:
    if not value or "." not in value:
        return False
    raw, signature = value.split(".", 1)
    try:
        expiry = int(raw)
    except ValueError:
        return False
    if expiry < _now():
        return False
    return hmac.compare_digest(signature, _sign(token, expiry))


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


class App:
    def __init__(self, config: Config):
        self.config = config
        self._base_config = config
        self.db_path = Path(config.db_path)
        self.token = config.web_token
        init_db(self.db_path, config.migrations_dir)
        jobs.recover_jobs(self.db_path)
        self.resolver = Resolver(self.db_path)
        self.arr = ArrData(config)
        self.play_service = None
        self.play_error = None
        self.guard: StreamGuard | None = None
        self._build_play_service()
        handlers = dict(admin.JOB_HANDLERS)
        handlers["publish-now"] = self._job_publish
        handlers["download-now"] = self._job_download
        handlers["sync-pending"] = self._job_sync_pending
        self.runner = jobs.JobRunner(self.db_path, config, handlers)
        self.runner.start()
        self.scheduler = Scheduler(self.db_path, self.runner)
        self.scheduler.start()

    def _job_sync(self, ctx, keys: list[str] | None = None) -> tuple[list[str], dict]:
        if keys is None:
            keys = [
                str(value) for value in ctx.params.get("keys") or [] if value
            ][:1000]
        if not keys:
            raise jobs.JobError("faltan keys")
        kind = "series" if ctx.params.get("kind") == "series" else "movie"
        summary = admin._sync_keys(
            ctx,
            keys,
            kind=kind,
            delay=float(ctx.params.get("delay") or 0.5),
            min_seeders=int(ctx.params.get("min_seeders") or 1),
            no_jackett=bool(ctx.params.get("no_jackett")),
        )
        return keys, summary

    def _job_publish(self, ctx) -> dict:
        keys, summary = self._job_sync(ctx)
        from .strm_inventory import invalidate

        written = 0
        skipped = 0
        to_notify: list[str] = []
        for index, key in enumerate(keys):
            ctx.check()
            ctx.report(
                f"publicando {index + 1}/{len(keys)}",
                progress=0.5 + 0.5 * (index / max(1, len(keys))),
            )
            report = admin.export_strm(
                self.db_path,
                self.config.catalog_library,
                base_url=self.config.playback_base_url,
                key=key,
                limit=None,
                skip_library=False,
            )
            written += int(report.get("written") or 0)
            skipped += int(report.get("skipped") or 0)
            to_notify.extend(report.get("written_paths") or [])
        invalidate(self.config.catalog_library)
        if to_notify:
            jellyfin.notify_updated(self.config, to_notify[:500])
        return {"sync": summary, "published": written, "skipped": skipped}

    def _job_download(self, ctx) -> dict:
        keys = [str(value) for value in ctx.params.get("keys") or [] if value][:1000]
        if not keys:
            raise jobs.JobError("faltan keys")
        if self.play_service is None:
            raise jobs.JobError(
                "reproducción no configurada: falta QBIT_PASS"
            )
        results = []
        for index, key in enumerate(keys):
            ctx.check()
            ctx.report(
                f"preparando descarga {index + 1}/{len(keys)}",
                progress=index / max(1, len(keys)),
            )
            # Solo hace falta buscar fuente para el título que se descarga.
            _, summary = self._job_sync(ctx, keys=[key])
            handle = admin.download_now(
                self.config,
                self.play_service,
                key,
                client_ip="web-job",
                force=True,
            )
            results.append({"key": key, "sync": summary, "download": handle})
        return {
            "requested": len(keys),
            "downloads": results,
            "download": results[0]["download"] if results else None,
            "sync": results[0]["sync"] if results else None,
        }

    def _job_sync_pending(self, ctx) -> dict:
        params = ctx.params
        publication = str(params.get("publication") or "pendiente")
        if publication not in ("pendiente", "sin_fuente"):
            publication = "pendiente"
        kind = params.get("kind") if params.get("kind") in ("movie", "series") else ""
        limit = int(params.get("limit") or 0)
        delay = max(0.0, float(params.get("delay") if params.get("delay") is not None else 1.0))
        min_seeders = int(params.get("min_seeders") if params.get("min_seeders") is not None else 1)
        no_jackett = bool(params.get("no_jackett"))
        targets = ondemand.pending_targets(
            self.db_path,
            self.config,
            publication=publication,
            kind=kind or "",
            q=str(params.get("q") or ""),
            limit=limit,
        )
        if not targets:
            raise jobs.JobError("no hay títulos que buscar con ese filtro")
        movies = [target["key"] for target in targets if target["kind"] == "movie"]
        series = [target["key"] for target in targets if target["kind"] == "series"]
        result: dict[str, Any] = {
            "publication": publication,
            "targets": len(targets),
            "movies": len(movies),
            "series_episodes": len(series),
            "delay": delay,
        }
        offset = 0.0
        for label, group, group_kind in (
            ("movies_result", movies, "movie"),
            ("series_result", series, "series"),
        ):
            if not group:
                continue
            span = len(group) / max(1, len(targets))
            ctx.report(
                f"buscando fuentes ({group_kind}: {len(group)})",
                progress=offset,
            )
            result[label] = admin._sync_keys(
                ctx,
                group,
                kind=group_kind,
                delay=delay,
                min_seeders=min_seeders,
                no_jackett=no_jackett,
                progress_base=offset,
                progress_span=span,
            )
            offset += span
        return result

    def _build_play_service(self) -> None:
        if self.guard is not None:
            try:
                self.guard.stop()
            except Exception:
                pass
            self.guard = None
        self.play_service = None
        self.play_error = None
        if not self.config.qbittorrent_pass:
            self.play_error = "sin QBIT_PASS"
            return
        try:
            from .play_service import PlayService

            qbit_read = QbittorrentClient.from_config(self.config)
            qbit_write = QbittorrentWriteClient.from_config(self.config)
            qbit_read.app_version()
            self.play_service = PlayService(
                str(self.db_path),
                qbit_read,
                qbit_write,
                data_path=str(self.config.downloads_root),
                save_path=str(self.config.ondemand_save_path),
                jackett_api_key=self.config.jackett_api_key,
                max_active=self.config.max_active_ondemand,
                max_size_bytes=self.config.max_stream_bytes,
                tmdb_api_key=self.config.tmdb_api_key,
                stream_roots=self.config.content_roots(),
            )
            self.guard = StreamGuard(str(self.db_path), self.play_service)
            self.guard.start()
        except Exception as exc:
            self.play_service = None
            self.play_error = str(exc)[:250]

    def reload_config(self) -> Config:
        """Relee la configuración efectiva y reconstruye los clientes."""
        base = replace(self._base_config, db_path=self.db_path)
        config = app_settings.load_config(base)
        self.config = config
        self.token = config.web_token
        self.resolver = Resolver(self.db_path)
        self.arr = ArrData(config)
        runner = getattr(self, "runner", None)
        if runner is not None:
            runner.config = config
        self._build_play_service()
        return config

    def qbit(self) -> tuple[QbittorrentClient | None, str | None]:
        if not self.config.qbittorrent_pass:
            return None, "sin QBIT_PASS"
        try:
            return QbittorrentClient.from_config(self.config), None
        except QbittorrentError as exc:
            return None, str(exc)[:250]

    def audit(
        self, kind: str, *, asset_id: int | None = None, payload: Any = None
    ) -> None:
        try:
            with closing(connect(self.db_path)) as conn:
                conn.execute(
                    "INSERT INTO events(kind, asset_id, payload) VALUES (?,?,?)",
                    (
                        kind[:80],
                        asset_id,
                        json.dumps(payload or {}, ensure_ascii=False)[:4000],
                    ),
                )
                conn.commit()
        except Exception:
            pass


PLAYBACK_PATHS = {
    "/play",
    "/resolve",
    "/sources",
    "/status",
    "/catalog/lookup",
    "/prepare",
    "/lease",
}


class Handler(PlaybackHandler):
    server_version = "tod/0.2"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    # El motor de reproducción lee estas propiedades de la app única.
    @property
    def resolver(self) -> Resolver:
        return self.app.resolver

    @property
    def db_path(self) -> str:  # type: ignore[override]
        return str(self.app.db_path)

    @property
    def play_service(self) -> Any | None:  # type: ignore[override]
        return self.app.play_service

    def _auto_prepare_allowed(self, key: str, resolution: Any) -> bool:
        """Permite arrancar el prepare si Jellyfin está reproduciendo esto.

        Un escaneo o un ffprobe suelto no tienen sesión activa, así que
        siguen recibiendo 403 y no disparan descargas.
        """
        try:
            return key in jellyfin.playing_keys(self.app.config)
        except Exception:
            return False

    @property
    def strm_roots(self) -> tuple[str, ...]:
        return self.app.config.strm_roots

    # ------------------------------------------------------------------ HTTP

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_GET(self) -> None:
        self._dispatch(head=False)

    def do_POST(self) -> None:
        self._dispatch(head=False, mutate=True)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Sin IPs ni rutas con secretos en el journal.
        print(f"web {self.command} {self.path.split('?', 1)[0]}", flush=True)

    def _dispatch(self, *, head: bool, mutate: bool = False) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = {
            key: values[-1]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        try:
            self._check_host()
            if path in PLAYBACK_PATHS:
                # Mismo proceso y mismo motor: la web y el proxy comparten
                # resolver, base de datos y PlayService.
                return PlaybackHandler._serve(self, head)
            if mutate:
                self._check_mutation()
            if path == "/" or path == "/index.html":
                return self._static("index.html", head=head)
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):], head=head)
            if path == "/healthz":
                return self._json(200, {"status": "ok"}, head=head)
            if not path.startswith("/api/"):
                raise WebError(404, "no encontrado")

            parts = [part for part in path.split("/") if part][1:]
            if parts == ["session"]:
                return self._session(head=head)
            if parts == ["login"]:
                return self._login(head=head)
            if parts == ["logout"]:
                return self._logout(head=head)

            self._require_auth()
            if mutate and not head:
                body = self._body()
            else:
                body = {}
            return self._api(parts, params, body, head=head, mutate=mutate)
        except WebError as exc:
            self._json(
                exc.status,
                {"error": exc.message, "detail": exc.detail},
                head=head,
            )
        except JobConflict as exc:
            self._json(409, {"error": str(exc)}, head=head)
        except JobError as exc:
            self._json(400, {"error": str(exc)}, head=head)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            traceback.print_exc()
            self._json(
                500,
                {"error": "error interno", "detail": str(exc)[:300]},
                head=head,
            )

    # ------------------------------------------------------------- seguridad

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]  # type: ignore[attr-defined]
        bind = (self.server.server_address[0] or "127.0.0.1").lower()  # type: ignore[attr-defined]
        machine = socket.gethostname().strip().lower()
        names = {
            "127.0.0.1",
            "localhost",
            "[::1]",
            bind,
            machine,
            f"{machine}.local",
        }
        # Permite abrir el panel por la IP de la LAN sin editar .env. El
        # socket UDP no envía tráfico: solo pregunta qué IP de salida hay.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("8.8.8.8", 80))
                names.add(probe.getsockname()[0])
            finally:
                probe.close()
        except OSError:
            pass
        try:
            for address in socket.gethostbyname_ex(machine)[2]:
                names.add(address)
        except OSError:
            pass
        extra = (
            os.environ.get("CATALOGO_WEB_ALLOWED_HOSTS")
            or self.app.config.web_allowed_hosts
            or ""
        )
        for name in extra.split(","):
            name = name.strip().lower()
            if name:
                names.add(name.split(":", 1)[0] if name.startswith("[") else name)
        allowed: set[str] = set()
        for name in names:
            if not name:
                continue
            if name.startswith("[") and name.endswith("]"):
                allowed.add(f"{name}:{port}")
            elif ":" in name:
                allowed.add(name)
            else:
                allowed.add(f"{name}:{port}")
        return allowed

    def _check_host(self) -> None:
        host = (self.headers.get("Host") or "").strip().lower()
        allowed = self._allowed_hosts()
        if host and host not in allowed:
            raise WebError(403, "host no permitido", {"host": host})

    def _check_mutation(self) -> None:
        # Una cabecera personalizada obliga a preflight en navegadores
        # (que no respondemos), así que bloquea CSRF de formularios.
        if (self.headers.get("X-Catalogo") or "").lower() != "web":
            raise WebError(403, "falta cabecera X-Catalogo")
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin:
            parsed = urllib.parse.urlparse(origin)
            if f"{parsed.netloc}" not in self._allowed_hosts():
                raise WebError(403, "origen no permitido", {"origin": origin})

    def _authenticated(self) -> bool:
        token = self.app.token
        if not token:
            return True
        cookies = self.headers.get("Cookie") or ""
        for chunk in cookies.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == COOKIE and _valid_cookie(token, value):
                return True
        return False

    def _require_auth(self) -> None:
        if not self._authenticated():
            raise WebError(401, "sesión requerida")

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            raise WebError(413, "cuerpo demasiado grande")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise WebError(400, "JSON inválido")
        if not isinstance(payload, dict):
            raise WebError(400, "se esperaba un objeto JSON")
        return payload

    # --------------------------------------------------------------- estático

    def _static(self, name: str, *, head: bool) -> None:
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".webp": "image/webp",
            ".woff2": "font/woff2",
            ".json": "application/json; charset=utf-8",
        }
        relative = (name or "").replace("\\", "/").lstrip("/")
        if not relative or ".." in relative.split("/"):
            raise WebError(404, "no encontrado")
        path = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            raise WebError(404, "no encontrado")
        content_type = types.get(path.suffix.lower())
        if not content_type:
            raise WebError(404, "no encontrado")
        try:
            content = path.read_bytes()
        except OSError:
            raise WebError(404, "no encontrado")
        etag = '"' + hashlib.md5(content).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        compressible = content_type.startswith(("text/", "image/svg", "application/json"))
        use_gzip = (
            compressible
            and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
            and len(content) > 512
        )
        body = gzip.compress(content, compresslevel=6) if use_gzip else content
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self._security_headers()
        if path.name == "index.html":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: https:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'",
            )
        self.end_headers()
        if not head:
            self.wfile.write(body)

    # ------------------------------------------------------------------- API

    def _api(
        self,
        parts: list[str],
        params: dict[str, str],
        body: dict[str, Any],
        *,
        head: bool,
        mutate: bool,
    ) -> None:
        app = self.app
        db = app.db_path

        if (
            len(parts) == 3
            and parts[0] == "releases"
            and parts[2] == "tracker"
            and not mutate
        ):
            return self._release_tracker(parts[1], head=head)

        if (
            len(parts) == 3
            and parts[0] == "releases"
            and parts[2] == "torrent"
            and not mutate
        ):
            return self._release_torrent(parts[1], head=head)

        if parts == ["overview"] and not mutate:
            qbit, error = app.qbit()
            return self._json(
                200,
                queries.overview(
                    db, config=app.config, qbit=qbit, qbit_error=error
                ),
                head=head,
            )

        if parts == ["events"] and not mutate:
            limit = self._int(params.get("limit"), 100)
            return self._json(200, {"items": queries.list_events(db, limit=limit)}, head=head)

        if parts == ["calendar"] and not mutate:
            days = self._int(params.get("days"), 90)
            return self._json(
                200,
                {
                    "days": days,
                    "items": queries.calendar(db, days=days),
                },
                head=head,
            )

        if parts == ["activity"] and not mutate:
            recent = jobs.list_jobs(
                db, limit=30, exclude_kinds=INTERNAL_JOB_KINDS
            )
            active = [
                job for job in recent
                if job.get("state") in ("queued", "running")
            ]
            latest = next(
                (job for job in recent if job.get("state") not in ("queued", "running")),
                None,
            )
            with closing(connect(db)) as conn:
                monitored = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM media_items WHERE monitored = 1"
                    ).fetchone()[0]
                )
            return self._json(
                200,
                {
                    "active": active,
                    "running": next(
                        (job for job in active if job.get("state") == "running"),
                        None,
                    ),
                    "queued": sum(1 for job in active if job.get("state") == "queued"),
                    "latest": latest,
                    "monitored": monitored,
                    "generated_at": int(time.time()),
                },
                head=head,
            )

        if parts == ["search"] and not mutate:
            return self._json(
                200,
                ondemand.search_catalog(
                    db,
                    params.get("q") or "",
                    limit=self._int(params.get("limit"), 8),
                ),
                head=head,
            )

        if parts and parts[0] == "collections":
            if len(parts) == 2 and not mutate:
                collection_id = self._int(parts[1], 0)
                if collection_id <= 0:
                    raise WebError(400, "colección inválida")
                data = admin.tmdb_collection(app.config, collection_id)
                data["parts"] = ondemand.collection_states(
                    db, app.config, data["parts"]
                )
                return self._json(200, data, head=head)
            if len(parts) == 3 and parts[2] == "add":
                if not mutate:
                    raise WebError(405, "usa POST")
                collection_id = self._int(parts[1], 0)
                if collection_id <= 0:
                    raise WebError(400, "colección inválida")
                action = str(body.get("action") or "catalog")
                if action not in ("catalog", "library"):
                    raise WebError(400, "acción no válida")
                result = admin.import_collection(app.config, collection_id)
                job_id = None
                pending_keys = []
                states = []
                if action == "library":
                    states = ondemand.collection_states(
                        db, app.config, result["collection"]["parts"]
                    )
                    pending_keys = [
                        part["key"]
                        for part in states
                        if part.get("in_catalog")
                        and not part.get("published")
                        and part.get("publication") != "sin_fuente"
                    ]
                    no_source_keys = [
                        part["key"]
                        for part in states
                        if part.get("in_catalog")
                        and not part.get("published")
                        and part.get("publication") == "sin_fuente"
                    ]
                    for part in states:
                        if not part.get("in_catalog"):
                            continue
                        admin.set_library(
                            app.config,
                            key=part["key"],
                            member=True,
                            monitored=True
                            if part.get("publication") == "sin_fuente"
                            else None,
                            source="collection",
                        )
                    skipped_no_source = len(no_source_keys)
                    monitored_count = len(no_source_keys)
                    if pending_keys:
                        job_id = app.runner.submit(
                            "publish-now",
                            {
                                "keys": pending_keys,
                                "kind": "movie",
                                "delay": 0.5,
                                "min_seeders": 1,
                            },
                            requested_by=self.client_address[0],
                            allow_duplicate=True,
                        )
                else:
                    skipped_no_source = 0
                    monitored_count = 0
                app.audit(
                    "web.collection_add",
                    payload={
                        "collection": collection_id,
                        "action": action,
                        "imported": result["imported"],
                        "keys": len(result["keys"]),
                        "pending": len(pending_keys),
                        "skipped_no_source": skipped_no_source,
                        "monitored": monitored_count,
                        "library": sum(1 for part in states if part.get("in_catalog")),
                        "job_id": job_id,
                    },
                )
                return self._json(
                    202,
                    {
                        **result,
                        "pending_keys": pending_keys,
                        "skipped_no_source": skipped_no_source,
                        "monitored": monitored_count,
                        "library": sum(1 for part in states if part.get("in_catalog")),
                        "job_id": job_id,
                    },
                    head=head,
                )
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "series":
            if len(parts) == 3 and parts[2] == "library":
                if not mutate:
                    raise WebError(405, "usa POST")
                tmdb = self._int(parts[1], 0)
                if tmdb <= 0:
                    raise WebError(400, "tmdb inválido")
                raw_seasons = body.get("seasons") or []
                if not isinstance(raw_seasons, list):
                    raise WebError(400, "seasons inválido")
                result = admin.set_library(
                    app.config,
                    series_tmdb=tmdb,
                    seasons=[
                        int(value)
                        for value in raw_seasons
                        if str(value).lstrip("-").isdigit()
                    ]
                    or None,
                    member=bool(body.get("member", True)),
                    monitored=body.get("monitored") if "monitored" in body else None,
                    source="manual",
                )
                job_id = None
                exported = 0
                jellyfin_scan: dict[str, Any] = {"ok": False, "reason": "sin_rutas"}
                if result.get("member"):
                    # Publica ya lo que ya está en Radarr/Sonarr o tiene
                    # release; el monitor solo persigue lo que falta.
                    for key in result.get("keys", []):
                        try:
                            report = admin.export_strm(
                                app.db_path,
                                app.config.catalog_library,
                                base_url=app.config.playback_base_url,
                                key=key,
                                limit=None,
                                skip_library=False,
                            )
                            exported += int(report.get("written") or 0)
                        except Exception:
                            continue
                    strm_inventory.invalidate(app.config.catalog_library)
                    keys = result.get("keys", [])
                    if keys:
                        with closing(connect(db)) as conn:
                            rows = conn.execute(
                                "SELECT * FROM media_items WHERE key IN ("
                                + ",".join("?" for _ in keys)
                                + ")",
                                keys,
                            ).fetchall()
                        paths = [
                            strm_module.strm_path(
                                app.config.catalog_library, dict(row)
                            )
                            for row in rows
                        ]
                        jellyfin_scan = jellyfin.notify_updated(
                            app.config,
                            [path for path in paths if path.exists()],
                        )
                if result.get("member") and body.get("monitored"):
                    published = strm_inventory.inventory(
                        app.config.catalog_library
                    )["keys"]
                    already = [
                        key for key in result.get("keys", []) if key in published
                    ]
                    if already:
                        with closing(connect(db)) as conn:
                            conn.execute(
                                "UPDATE media_items SET monitored = 0 "
                                "WHERE key IN ("
                                + ",".join("?" for _ in already)
                                + ")",
                                already,
                            )
                            conn.commit()
                    pending = [
                        key
                        for key in result.get("keys", [])
                        if key not in published
                    ]
                    if pending:
                        try:
                            job_id = app.runner.submit(
                                "monitor",
                                {
                                    "keys": pending,
                                    "limit": len(pending),
                                    "delay": 1,
                                },
                                requested_by=self.client_address[0],
                            )
                        except JobConflict:
                            job_id = None
                app.audit(
                    "web.series_library",
                    payload={
                        **result,
                        "job_id": job_id,
                        "exported": exported,
                        "jellyfin_scan": jellyfin_scan,
                    },
                )
                return self._json(
                    200,
                    {
                        **result,
                        "job_id": job_id,
                        "exported": exported,
                        "jellyfin_scan": jellyfin_scan,
                    },
                    head=head,
                )
            if len(parts) == 3 and parts[2] == "unpublish":
                if not mutate:
                    raise WebError(405, "usa POST")
                tmdb = self._int(parts[1], 0)
                if tmdb <= 0:
                    raise WebError(400, "tmdb inválido")
                result = admin.remove_series_from_library(app.config, tmdb)
                app.audit("web.series_unpublish", payload=result)
                return self._json(200, result, head=head)
            if not mutate and len(parts) == 2:
                tmdb = self._int(parts[1], 0)
                if tmdb <= 0:
                    raise WebError(400, "tmdb inválido")
                qbit, _ = app.qbit()
                tmdb_data = None
                tmdb_error = None
                try:
                    tmdb_data = admin.tmdb_series_full(app.config, tmdb)
                except Exception as exc:
                    tmdb_error = str(exc)[:200]
                detail = ondemand.series_detail(
                    db,
                    app.config,
                    tmdb,
                    qbit=qbit,
                    arr=app.arr,
                    tmdb_data=tmdb_data,
                )
                if detail is None:
                    raise WebError(404, "serie no encontrada")
                detail["tmdb_error"] = tmdb_error
                if not detail["series"].get("poster_path"):
                    detail["series"]["poster_path"] = admin.tmdb_poster(
                        app.config, "series", tmdb
                    )
                return self._json(200, detail, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "ondemand":
            return self._ondemand(parts, params, body, head=head, mutate=mutate)

        if parts == ["trackers"] and not mutate:
            return self._json(200, {"items": queries.list_trackers(db)}, head=head)

        if parts and parts[0] == "titles":
            return self._titles(parts, params, body, head=head, mutate=mutate)

        if parts and parts[0] == "tmdb":
            return self._tmdb(parts, params, head=head)

        if parts and parts[0] == "releases":
            return self._releases(parts, body, mutate=mutate, head=head)

        if parts == ["downloads"] and not mutate:
            qbit, error = app.qbit()
            return self._json(
                200,
                queries.list_downloads(
                    db, qbit, category=params.get("category") or None
                ),
                head=head,
            )

        if len(parts) == 3 and parts[0] == "downloads" and parts[2] == "action":
            if not mutate:
                raise WebError(405, "usa POST")
            info_hash = parts[1]
            if not HASH_RE.match(info_hash):
                raise WebError(400, "hash inválido")
            result = admin.qbit_action(
                app.config,
                info_hash,
                str(body.get("action") or ""),
                delete_files=bool(body.get("delete_files")),
            )
            app.audit("web.qbit_action", payload=result)
            return self._json(200, result, head=head)

        if parts == ["seeds"] and not mutate:
            qbit, _ = app.qbit()
            return self._json(
                200,
                queries.list_seeds(
                    db,
                    limit=self._int(params.get("limit"), 100),
                    qbit=qbit,
                ),
                head=head,
            )

        if len(parts) == 3 and parts[0] == "seeds" and parts[2] == "finish":
            if not mutate:
                raise WebError(405, "usa POST")
            intent_id = self._int(parts[1], 0)
            with closing(connect(db)) as conn:
                row = conn.execute(
                    """
                    SELECT i.info_hash, i.started_at,
                           t.seed_started_at,
                           COALESCE(i.info_hash, t.info_hash) AS effective_hash
                    FROM seed_commitments i
                    LEFT JOIN torrents t ON t.id = (
                        SELECT id FROM torrents WHERE release_id = i.release_id
                        ORDER BY id DESC LIMIT 1
                    )
                    WHERE i.id = ?
                    """,
                    (intent_id,),
                ).fetchone()
            live_state = None
            live_ratio = None
            live_started = None
            if row is not None and row["effective_hash"]:
                qbit, _ = app.qbit()
                if qbit is not None:
                    try:
                        torrent = qbit.torrent(str(row["effective_hash"]))
                    except Exception:
                        torrent = None
                    if torrent is not None:
                        raw = str(torrent.get("state") or "").lower()
                        live_state = "seeding" if any(
                            value in raw
                            for value in ("uploading", "stalledup", "forcedup")
                        ) else "active"
                        live_ratio = float(torrent.get("ratio") or 0)
                        live_started = row["seed_started_at"] or row["started_at"]
            result = finish_intent(
                str(db),
                intent_id,
                state=live_state,
                ratio=live_ratio,
                started=live_started,
            )
            app.audit("web.seed_finish", payload=result)
            return self._json(200, result, head=head)

        if parts == ["monitor"] and not mutate:
            data = ondemand.monitored_items(
                db,
                app.config,
                q=params.get("q") or "",
                state=params.get("state") or "",
                kind=params.get("kind") or "",
                page=self._int(params.get("page"), 1),
                per_page=self._int(params.get("per_page"), 50),
            )
            data["health"] = self._monitor_health(db)
            return self._json(200, data, head=head)

        if len(parts) == 3 and parts[0] == "monitor" and parts[1] == "indexers":
            if not mutate:
                raise WebError(405, "usa POST")
            indexer_id = self._int(parts[2], 0)
            if indexer_id <= 0:
                raise WebError(400, "indexer inválido")
            enabled = bool(body.get("enabled", True))
            try:
                from .prowlarr import ProwlarrClient

                result = ProwlarrClient.from_config(
                    app.config
                ).set_indexer_enabled(indexer_id, enabled)
            except Exception as exc:
                raise WebError(503, "no se pudo cambiar el indexer", str(exc)[:300])
            _INDEXER_CACHE.pop(str(app.config.prowlarr_url), None)
            app.audit(
                "web.indexer_toggle",
                payload={
                    "indexer": indexer_id,
                    "enabled": enabled,
                    "name": result.get("name"),
                },
            )
            return self._json(
                200,
                {
                    "id": indexer_id,
                    "name": result.get("name"),
                    "enabled": bool(result.get("enable", enabled)),
                },
                head=head,
            )

        if parts == ["jobs"]:
            if mutate:
                kind = str(body.get("kind") or "").strip()
                if kind not in admin.JOB_HANDLERS:
                    raise WebError(400, "tipo de tarea no permitido", {"kind": kind})
                raw_params = body.get("params") or {}
                if not isinstance(raw_params, dict):
                    raise WebError(400, "params debe ser un objeto")
                keys = raw_params.get("keys")
                if keys is not None:
                    if not isinstance(keys, list) or len(keys) > 200:
                        raise WebError(400, "keys inválidas")
                    raw_params["keys"] = [str(key)[:200] for key in keys]
                job_id = app.runner.submit(
                    kind,
                    raw_params,
                    requested_by=self.client_address[0],
                )
                app.audit("web.job", payload={"kind": kind, "job_id": job_id, "params": raw_params})
                return self._json(
                    202,
                    {"job_id": job_id, "job": jobs.get_job(db, job_id)},
                    head=head,
                )
            state = params.get("state") or None
            return self._json(
                200,
                {
                    "items": jobs.list_jobs(
                        db,
                        limit=self._int(params.get("limit"), 50),
                        state=state,
                        exclude_kinds=INTERNAL_JOB_KINDS,
                    )
                },
                head=head,
            )

        if len(parts) == 2 and parts[0] == "jobs":
            job_id = self._int(parts[1], 0)
            job = jobs.get_job(db, job_id)
            if job is None:
                raise WebError(404, "tarea no encontrada")
            return self._json(200, job, head=head)

        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
            if not mutate:
                raise WebError(405, "usa POST")
            job = app.runner.cancel(self._int(parts[1], 0))
            if job is None:
                raise WebError(404, "tarea no encontrada")
            app.audit("web.job_cancel", payload={"job_id": job["id"]})
            return self._json(200, job, head=head)

        if parts == ["settings"]:
            if not mutate:
                return self._json(
                    200,
                    {
                        **app_settings.serialize(app.config),
                        "paths": {
                            setting.key: app_settings.path_status(
                                getattr(app.config, setting.attr)
                            )
                            for setting in app_settings.SETTINGS
                            if setting.kind == "path"
                            and getattr(app.config, setting.attr, None)
                            and not (
                                setting.key == "library_root"
                                and (
                                    app.config.library_root_movies
                                    or app.config.library_root_series
                                )
                            )
                        },
                    },
                    head=head,
                )
            raw_values = body.get("values")
            if raw_values is None:
                raw_values = {
                    key: value
                    for key, value in body.items()
                    if key in app_settings.BY_KEY
                }
            if not isinstance(raw_values, dict):
                raise WebError(400, "values debe ser un objeto")
            clear = body.get("clear") or []
            if not isinstance(clear, list):
                raise WebError(400, "clear debe ser una lista")
            try:
                result = app_settings.save_settings(
                    app.config.db_path, raw_values, clear=clear
                )
            except ValueError as exc:
                raise WebError(400, str(exc)) from exc
            config = app.reload_config()
            try:
                app.runner.submit("attention-check", requested_by="settings")
            except JobConflict:
                pass
            app.audit(
                "web.settings_save",
                payload={
                    "saved": result["saved"],
                    "cleared": result["cleared"],
                },
            )
            return self._json(
                200,
                {
                    **result,
                    **app_settings.serialize(config),
                    "restart_labels": [
                        app_settings.BY_KEY[key].label
                        for key in result.get("restart_required", [])
                        if key in app_settings.BY_KEY
                    ],
                },
                head=head,
            )

        if parts == ["settings", "path-check"]:
            if not mutate:
                raise WebError(405, "usa POST")
            key = str(body.get("key") or "")
            raw_path = body.get("path")
            if not raw_path and key:
                setting = app_settings.BY_KEY.get(key)
                if setting is None:
                    raise WebError(400, "ajuste desconocido")
                raw_path = getattr(app.config, setting.attr, None)
            if not raw_path:
                raise WebError(400, "falta path")
            return self._json(
                200, app_settings.path_status(str(raw_path)), head=head
            )

        if (
            len(parts) == 3
            and parts[0] == "integrations"
            and parts[2] == "test"
        ):
            if not mutate:
                raise WebError(405, "usa POST")
            values = body.get("values") or {}
            if not isinstance(values, dict):
                raise WebError(400, "values debe ser un objeto")
            try:
                config = app_settings.preview_config(app.config, values)
                result = integrations.test_integration(parts[1], config)
            except (ValueError, integrations.IntegrationError) as exc:
                raise WebError(400, str(exc)) from exc
            app.audit(
                "web.integration_test",
                payload={
                    "name": parts[1],
                    "ok": result.get("ok"),
                    "latency_ms": result.get("latency_ms"),
                },
            )
            return self._json(200, result, head=head)

        if parts == ["scheduler"] and not mutate:
            return self._json(
                200, {"items": list_tasks(app.db_path)}, head=head
            )

        if len(parts) == 2 and parts[0] == "scheduler":
            if not mutate:
                raise WebError(405, "usa POST")
            try:
                state = save_task(app.db_path, parts[1], body)
            except ValueError as exc:
                raise WebError(400, str(exc)) from exc
            app.audit(
                "web.scheduler_save",
                payload={"task": parts[1], **body},
            )
            item = next(
                (
                    task
                    for task in list_tasks(app.db_path)
                    if task["name"] == parts[1]
                ),
                None,
            )
            return self._json(200, item or state, head=head)

        if (
            len(parts) == 3
            and parts[0] == "scheduler"
            and parts[2] == "run"
        ):
            if not mutate:
                raise WebError(405, "usa POST")
            try:
                result = app.scheduler.run_now(parts[1])
            except ValueError as exc:
                raise WebError(400, str(exc)) from exc
            except JobConflict as exc:
                raise WebError(409, str(exc)) from exc
            app.audit("web.scheduler_run", payload=result)
            return self._json(202, result, head=head)

        if parts == ["attention"]:
            if mutate:
                raise WebError(405, "usa GET")
            return self._json(
                200,
                attention.status(db, app.config),
                head=head,
            )

        if parts == ["attention", "check"]:
            if not mutate:
                raise WebError(405, "usa POST")
            job_id = app.runner.submit("attention-check", requested_by="web")
            app.audit("web.attention_check", payload={"job_id": job_id})
            return self._json(202, {"job_id": job_id}, head=head)

        if parts == ["setup"] and not mutate:
            config = app.config
            capabilities = {
                "qbittorrent": bool(config.qbittorrent_pass),
                "prowlarr": bool(config.prowlarr_api_key),
                "jackett": bool(config.jackett_api_key),
                "jellyfin": bool(config.jellyfin_api_key),
                "radarr": bool(config.radarr_api_key),
                "sonarr": bool(config.sonarr_api_key),
                "tmdb": bool(config.tmdb_api_key),
            }
            paths = {
                "catalog_library": app_settings.path_status(
                    config.catalog_library
                ),
                "downloads_root": app_settings.path_status(
                    config.downloads_root
                ),
            }
            missing = [
                name for name, ok in capabilities.items()
                if not ok and name in ("qbittorrent", "jellyfin", "tmdb")
            ]
            if not (capabilities["prowlarr"] or capabilities["jackett"]):
                missing.append("prowlarr")
            if not paths["catalog_library"].get("exists"):
                missing.append("catalog_library")
            return self._json(
                200,
                {
                    "capabilities": capabilities,
                    "missing": missing,
                    "paths": paths,
                },
                head=head,
            )

        if parts == ["system"] and not mutate:
            return self._json(200, self._system(), head=head)

        if parts == ["system", "qbit-limits"]:
            if not mutate:
                raise WebError(405, "usa POST")
            result = admin.apply_qbit_limits(
                app.config,
                radarr_kbps=self._int(body.get("radarr_kbps"), admin.DEFAULT_RADARR_KBPS),
                sonarr_kbps=self._int(body.get("sonarr_kbps"), admin.DEFAULT_SONARR_KBPS),
                ondemand_kbps=self._int(body.get("ondemand_kbps"), admin.DEFAULT_ONDEMAND_KBPS),
                dry_run=bool(body.get("dry_run")),
            )
            app.audit("web.qbit_limits", payload=result)
            return self._json(200, result, head=head)

        if parts == ["trackers", "block"]:
            if not mutate:
                raise WebError(405, "usa POST")
            result = admin.block_tracker(
                app.config,
                str(body.get("tracker") or ""),
                blocked=bool(body.get("blocked", True)),
                reason=str(body.get("reason") or "downloads_broken"),
                dry_run=bool(body.get("dry_run")),
            )
            app.audit("web.tracker_block", payload=result)
            return self._json(200, result, head=head)

        raise WebError(404, "no encontrado")

    def _titles(
        self,
        parts: list[str],
        params: dict[str, str],
        body: dict[str, Any],
        *,
        head: bool,
        mutate: bool,
    ) -> None:
        app = self.app
        db = app.db_path

        if len(parts) == 1:
            if mutate:
                kind = str(body.get("kind") or "movie")
                tmdb = self._int(body.get("tmdb"), 0)
                if not tmdb:
                    raise WebError(400, "falta tmdb")
                if kind == "series":
                    seasons = body.get("seasons") or []
                    if not isinstance(seasons, list) or not seasons:
                        raise WebError(400, "elige al menos una temporada")
                    seasons = [self._int(value, 0) for value in seasons]
                    seasons = [value for value in seasons if value > 0]
                    if not seasons:
                        raise WebError(400, "temporadas inválidas")
                    result = admin.add_series(app.config, tmdb, seasons)
                    app.audit("web.add_series", payload={
                        "tmdb": tmdb, "seasons": seasons,
                        "episodes": result.get("episodes"),
                    })
                else:
                    result = admin.add_movie(app.config, tmdb)
                    app.audit("web.add_movie", payload={"tmdb": tmdb, "key": result.get("key")})
                return self._json(200, result, head=head)
            return self._json(
                200,
                queries.list_titles(
                    db,
                    q=params.get("q") or "",
                    kind=params.get("kind") or "",
                    status=params.get("status") or "",
                    availability=params.get("availability") or "",
                    page=self._int(params.get("page"), 1),
                    per_page=self._int(params.get("per_page"), 25),
                    sort=params.get("sort") or "popularity",
                ),
                head=head,
            )

        asset_id = self._int(parts[1], 0)
        if asset_id <= 0:
            raise WebError(400, "id inválido")
        if len(parts) == 2 and not mutate:
            qbit, _ = app.qbit()
            detail = queries.title_detail(db, asset_id, qbit=qbit)
            if detail is None:
                raise WebError(404, "título no encontrado")
            item = detail["item"]
            if item.get("tmdb"):
                detail["poster_path"] = item.get("poster_path") or admin.tmdb_poster(
                    app.config,
                    item["kind"],
                    int(item["tmdb"]),
                )
            detail["ondemand"] = ondemand.item_state(
                db, app.config, detail["item"], qbit=qbit, arr=app.arr
            )
            if detail["item"].get("kind") == "movie" and detail["item"].get("tmdb"):
                try:
                    detail["collection"] = admin.tmdb_movie_collection(
                        app.config, int(detail["item"]["tmdb"])
                    )
                except Exception:
                    detail["collection"] = None
            return self._json(200, detail, head=head)

        if len(parts) == 3 and parts[2] == "status" and not mutate:
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT key FROM media_items WHERE id=?", (asset_id,)
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            qbit, _ = app.qbit()
            return self._json(
                200,
                asset_status(str(db), row["key"], qbit=qbit) or {},
                head=head,
            )

        if len(parts) == 3 and parts[2] == "library":
            if not mutate:
                raise WebError(405, "usa POST")
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT * FROM media_items WHERE id=?", (asset_id,)
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            result = admin.set_library(
                app.config,
                key=row["key"],
                member=bool(body.get("member", True)),
                monitored=body.get("monitored") if "monitored" in body else None,
                source="manual",
            )
            jellyfin_scan: dict[str, Any] = {"ok": False, "reason": "sin_rutas"}
            if result.get("member"):
                path = strm_module.strm_path(
                    app.config.catalog_library, dict(row)
                )
                if path.exists():
                    jellyfin_scan = jellyfin.notify_updated(
                        app.config, [path]
                    )
            app.audit(
                "web.title_library",
                asset_id=asset_id,
                payload={**result, "jellyfin_scan": jellyfin_scan},
            )
            return self._json(
                200, {**result, "jellyfin_scan": jellyfin_scan}, head=head
            )

        if len(parts) == 3 and parts[2] == "monitor":
            if not mutate:
                raise WebError(405, "usa POST")
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT key FROM media_items WHERE id=?", (asset_id,)
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            monitored = bool(body.get("monitored", True))
            result = admin.set_monitored(
                app.config, row["key"], monitored=monitored, source="manual"
            )
            job_id = None
            if monitored:
                try:
                    job_id = app.runner.submit(
                        "monitor",
                        {"keys": [row["key"]], "limit": 1, "delay": 0},
                        requested_by=self.client_address[0],
                    )
                except JobConflict:
                    job_id = None
            app.audit(
                "web.title_monitor",
                asset_id=asset_id,
                payload={"key": row["key"], "monitored": monitored, "job_id": job_id},
            )
            return self._json(
                200, {**result, "job_id": job_id}, head=head
            )

        if len(parts) == 3 and parts[2] == "unpublish":
            if not mutate:
                raise WebError(405, "usa POST")
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT key FROM media_items WHERE id=?", (asset_id,)
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            result = admin.remove_from_library(app.config, row["key"])
            app.audit(
                "web.library_remove",
                asset_id=asset_id,
                payload={"key": row["key"], "removed": result["removed"]},
            )
            return self._json(200, result, head=head)

        if len(parts) == 3 and parts[2] == "manual-search":
            if not mutate:
                raise WebError(405, "usa POST")
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT key FROM media_items WHERE id=?", (asset_id,)
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            try:
                result = admin.manual_search(
                    app.config,
                    row["key"],
                    min_seeders=self._int(body.get("min_seeders"), 0),
                    no_jackett=bool(body.get("no_jackett")),
                    query=str(body.get("query") or "").strip() or None,
                )
            except Exception as exc:
                raise WebError(400, "no se pudo buscar", str(exc)[:300])
            app.audit(
                "web.manual_search",
                asset_id=asset_id,
                payload={
                    "key": row["key"],
                    "releases": len(result.get("releases") or []),
                },
            )
            return self._json(200, result, head=head)

        if len(parts) == 3 and parts[2] in ("sync", "export"):
            if not mutate:
                raise WebError(405, "usa POST")
            with closing(connect(db)) as conn:
                row = conn.execute(
                    "SELECT id, key, kind FROM media_items WHERE id=?",
                    (asset_id,),
                ).fetchone()
            if row is None:
                raise WebError(404, "título no encontrado")
            keys = [row["key"]]
            if isinstance(body.get("keys"), list):
                keys = [str(key)[:200] for key in body["keys"]][:200]
            kind = "series" if row["kind"] == "series" else "movie"
            if parts[2] == "sync":
                job_kind = "sync-sources"
                job_params = {
                    "keys": keys,
                    "kind": kind,
                    "delay": float(body.get("delay") or 0.5),
                    "min_seeders": self._int(body.get("min_seeders"), 1),
                    "no_jackett": bool(body.get("no_jackett")),
                }
            else:
                job_kind = "export-strm"
                job_params = {
                    "keys": keys,
                    "min_seeders": self._int(body.get("min_seeders"), 1),
                    "versions": body.get("versions") or "available",
                    "require_health": bool(body.get("require_health")),
                }
            job_id = app.runner.submit(
                job_kind, job_params, requested_by=self.client_address[0]
            )
            app.audit(
                "web.title_" + parts[2],
                asset_id=asset_id,
                payload={"job_id": job_id, "keys": len(keys)},
            )
            return self._json(
                202,
                {"job_id": job_id, "job": jobs.get_job(db, job_id)},
                head=head,
            )

        raise WebError(404, "no encontrado")

    def _ondemand(
        self,
        parts: list[str],
        params: dict[str, str],
        body: dict[str, Any],
        *,
        head: bool,
        mutate: bool,
    ) -> None:
        app = self.app
        db = app.db_path

        if parts == ["ondemand", "overview"] and not mutate:
            qbit, _ = app.qbit()
            return self._json(
                200,
                ondemand.overview(
                    db, app.config, qbit=qbit, arr=app.arr
                ),
                head=head,
            )

        if parts == ["ondemand", "catalog"] and not mutate:
            qbit, _ = app.qbit()
            return self._json(
                200,
                ondemand.catalog(
                    db,
                    app.config,
                    qbit=qbit,
                    arr=app.arr,
                    q=params.get("q") or "",
                    kind=params.get("kind") or "",
                    publication=params.get("publication") or "",
                    download=params.get("download") or "",
                    library=params.get("library") or "",
                    incomplete=params.get("incomplete") == "1",
                    page=self._int(params.get("page"), 1),
                    per_page=self._int(params.get("per_page"), 25),
                    group=params.get("group") or "episode",
                    sort=params.get("sort") or "popularity",
                    facets=params.get("facets") != "0",
                ),
                head=head,
            )

        if parts == ["ondemand", "pending-summary"] and not mutate:
            publication = params.get("publication") or "pendiente"
            if publication not in ("pendiente", "sin_fuente"):
                raise WebError(400, "publicación no válida")
            return self._json(
                200,
                ondemand.pending_summary(
                    db,
                    app.config,
                    publication=publication,
                    kind=params.get("kind") or "",
                    q=params.get("q") or "",
                ),
                head=head,
            )

        if parts == ["ondemand", "sync-pending"]:
            if not mutate:
                raise WebError(405, "usa POST")
            publication = str(body.get("publication") or "pendiente")
            if publication not in ("pendiente", "sin_fuente"):
                raise WebError(400, "publicación no válida")
            kind = str(body.get("kind") or "")
            if kind not in ("", "movie", "series"):
                raise WebError(400, "tipo no válido")
            params = {
                "publication": publication,
                "kind": kind,
                "q": str(body.get("q") or "")[:200],
                "limit": max(0, self._int(body.get("limit"), 0)),
                "delay": max(0.0, float(body.get("delay") if body.get("delay") is not None else 1.0)),
                "min_seeders": max(0, self._int(body.get("min_seeders") if body.get("min_seeders") is not None else 1, 1)),
                "no_jackett": bool(body.get("no_jackett")),
            }
            job_id = app.runner.submit(
                "sync-pending", params, requested_by=self.client_address[0]
            )
            app.audit(
                "web.sync_pending",
                payload={"job_id": job_id, **params},
            )
            return self._json(
                202,
                {"job_id": job_id, "job": jobs.get_job(db, job_id)},
                head=head,
            )

        if parts == ["ondemand", "settings"]:
            if mutate:
                try:
                    value = settings.set_ondemand_max_active(
                        db, self._int(body.get("max_active"), 0)
                    )
                except ValueError as exc:
                    raise WebError(400, str(exc))
                app.audit(
                    "web.ondemand_limit", payload={"max_active": value}
                )
                return self._json(
                    200,
                    {
                        "max_active": value,
                        "min": settings.MIN_MAX_ACTIVE,
                        "max": settings.MAX_MAX_ACTIVE,
                    },
                    head=head,
                )
            return self._json(
                200,
                {
                    "max_active": settings.ondemand_max_active(
                        db, getattr(app.config, "max_active_ondemand", 4)
                    ),
                    "min": settings.MIN_MAX_ACTIVE,
                    "max": settings.MAX_MAX_ACTIVE,
                },
                head=head,
            )

        if parts == ["ondemand", "publish"]:
            if not mutate:
                raise WebError(405, "usa POST")
            keys = body.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                raise WebError(400, "faltan keys")
            keys = [str(key)[:200] for key in keys][:1000]
            job_id = app.runner.submit(
                "export-strm",
                {"keys": keys},
                requested_by=self.client_address[0],
            )
            app.audit(
                "web.ondemand_publish", payload={"job_id": job_id, "keys": len(keys)}
            )
            return self._json(
                202,
                {"job_id": job_id, "job": jobs.get_job(db, job_id)},
                head=head,
            )

        if parts == ["ondemand", "acquire"]:
            if not mutate:
                raise WebError(405, "usa POST")
            action = str(body.get("action") or "").strip()
            if action not in ("publish", "download"):
                raise WebError(400, "acción no válida")
            keys = body.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                raise WebError(400, "faltan keys")
            keys = [str(key)[:200] for key in keys][:1000]
            kind = "series" if body.get("kind") == "series" else "movie"
            if action == "publish":
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                with closing(connect(db)) as conn:
                    conn.execute(
                        "UPDATE media_items SET library_added = 1, "
                        "library_added_at = COALESCE(library_added_at, ?), "
                        "library_source = COALESCE(library_source, 'bulk') "
                        "WHERE key IN ("
                        + ",".join("?" for _ in keys)
                        + ")",
                        [stamp, *keys],
                    )
                    conn.commit()
            job_kind = "publish-now" if action == "publish" else "download-now"
            job_id = app.runner.submit(
                job_kind,
                {
                    "keys": keys,
                    "kind": kind,
                    "delay": 0.5,
                    "min_seeders": 1,
                },
                requested_by=self.client_address[0],
                allow_duplicate=True,
            )
            app.audit(
                "web.ondemand_acquire",
                payload={
                    "job_id": job_id,
                    "action": action,
                    "keys": len(keys),
                },
            )
            return self._json(
                202,
                {"job_id": job_id, "job": jobs.get_job(db, job_id)},
                head=head,
            )

        if parts == ["ondemand", "download"]:
            if not mutate:
                raise WebError(405, "usa POST")
            key = str(body.get("key") or "").strip()
            if not key:
                raise WebError(400, "falta key")
            release_id = self._int(body.get("release_id"), 0) or None
            try:
                result = admin.download_now(
                    app.config,
                    app.play_service,
                    key,
                    release_id=release_id,
                    client_ip=self.client_address[0],
                    force=True,
                )
            except Exception as exc:
                detail = getattr(exc, "decision", None) or str(exc)[:300]
                raise WebError(503, "no se pudo descargar", detail)
            app.audit(
                "web.ondemand_download",
                payload={
                    "key": key,
                    "release_id": result.get("release_id"),
                    "info_hash": result.get("info_hash"),
                    "forced": True,
                },
            )
            return self._json(202, result, head=head)

        raise WebError(404, "no encontrado")

    def _tmdb(
        self, parts: list[str], params: dict[str, str], *, head: bool
    ) -> None:
        app = self.app
        if len(parts) == 2 and parts[1] == "search":
            kind = params.get("kind") or "movie"
            result = admin.tmdb_search(
                app.config,
                params.get("q") or "",
                kind="series" if kind == "series" else "movie",
                year=self._int(params.get("year"), 0) or None,
                page=self._int(params.get("page"), 1),
            )
            downloaded_ids = ondemand.downloaded_asset_ids(app.db_path)
            keys = strm_inventory.inventory(app.config.catalog_library)["keys"]
            for card in result.get("results") or []:
                if not card.get("tmdb"):
                    continue
                kind_card = card.get("kind") or "movie"
                tmdb = int(card["tmdb"])
                lib = app.arr.in_library(kind_card, tmdb)
                card["in_library"] = bool(lib.get("in_library"))
                card["library_has_file"] = bool(lib.get("has_file"))
                with closing(connect(app.db_path)) as conn:
                    rows = conn.execute(
                        """
                        SELECT m.id, m.key,
                               EXISTS(SELECT 1 FROM releases r
                                      WHERE r.asset_id = m.id) AS has_release
                        FROM media_items m
                        WHERE m.kind = ? AND m.tmdb = ?
                        """,
                        (kind_card, tmdb),
                    ).fetchall()
                if not rows:
                    card["catalog_state"] = None
                    card["catalog_downloaded"] = False
                    continue
                card["catalog_downloaded"] = any(
                    int(row["id"]) in downloaded_ids for row in rows
                )
                if any(
                    strm_inventory.is_key_published(dict(row), keys)
                    for row in rows
                ):
                    card["catalog_state"] = "publicado"
                elif any(int(row["has_release"] or 0) for row in rows):
                    card["catalog_state"] = "pendiente"
                else:
                    card["catalog_state"] = "sin_fuente"
            return self._json(200, result, head=head)
        if len(parts) == 3 and parts[1] == "seasons":
            result = admin.tmdb_seasons(app.config, self._int(parts[2], 0))
            return self._json(200, result, head=head)
        if len(parts) == 4 and parts[1] == "episodes":
            result = admin.tmdb_episodes(
                app.config, self._int(parts[2], 0), self._int(parts[3], 0)
            )
            return self._json(200, result, head=head)
        raise WebError(404, "no encontrado")

    def _releases(
        self,
        parts: list[str],
        body: dict[str, Any],
        *,
        mutate: bool,
        head: bool,
    ) -> None:
        app = self.app
        db = app.db_path
        release_id = self._int(parts[1], 0) if len(parts) > 1 else 0
        if release_id <= 0:
            raise WebError(400, "release inválido")
        with closing(connect(db)) as conn:
            row = conn.execute(
                """
                SELECT r.id, r.asset_id, m.key, m.title
                FROM releases r JOIN media_items m ON m.id = r.asset_id
                WHERE r.id = ?
                """,
                (release_id,),
            ).fetchone()
        if row is None:
            raise WebError(404, "release no encontrado")

        if len(parts) == 3 and parts[2] == "target":
            if not mutate:
                raise WebError(405, "usa POST")
            source_kind = str(body.get("source_kind") or "auto")
            result = admin.set_target(
                app.config,
                row["key"],
                source_kind,
                release_id=release_id,
                file_id=self._int(body.get("file_id"), 0) or None,
                note=str(body.get("note") or "")[:300] or None,
            )
            app.audit(
                "web.set_target", asset_id=row["asset_id"], payload=result
            )
            return self._json(200, result, head=head)

        if len(parts) == 3 and parts[2] == "health":
            if not mutate:
                raise WebError(405, "usa POST")
            job_id = app.runner.submit(
                "health",
                {
                    "key": row["key"],
                    "limit": 1,
                    "delay": 0,
                    "dry_run": bool(body.get("dry_run")),
                },
                requested_by=self.client_address[0],
            )
            app.audit(
                "web.release_health",
                asset_id=row["asset_id"],
                payload={"job_id": job_id, "release_id": release_id},
            )
            return self._json(
                202, {"job_id": job_id, "job": jobs.get_job(db, job_id)}, head=head
            )

        if len(parts) == 3 and parts[2] == "prepare":
            if not mutate:
                raise WebError(405, "usa POST")
            try:
                result = admin.prepare_release(
                    app.config,
                    app.play_service,
                    release_id,
                    client_ip=self.client_address[0],
                )
            except Exception as exc:
                detail = getattr(exc, "decision", None) or str(exc)[:300]
                raise WebError(503, "no se pudo preparar", detail)
            try:
                radarr, sonarr = admin._arr_clients(app.config)
                published = admin.export_strm(
                    app.config.db_path,
                    app.config.catalog_library,
                    base_url=app.config.playback_base_url,
                    key=result.get("key"),
                    limit=None,
                    min_seeders=1,
                    versions_filter="available",
                    radarr=radarr,
                    sonarr=sonarr,
                )
                paths = published.get("written_paths") or []
                if paths:
                    strm_inventory.invalidate(app.config.catalog_library)
                    jellyfin.notify_updated(app.config, paths[:100])
                result["strm"] = {
                    "written": published.get("written", 0),
                    "paths": paths[:10],
                }
            except Exception as exc:
                result["strm"] = {"error": str(exc)[:200]}
            app.audit(
                "web.prepare", asset_id=row["asset_id"], payload={
                    "release_id": release_id,
                    "info_hash": result.get("info_hash"),
                },
            )
            return self._json(202, result, head=head)

        raise WebError(404, "no encontrado")

    def _monitor_health(self, db) -> dict[str, Any]:
        app = self.app
        recent = jobs.list_jobs(db, limit=100)

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

        indexers: list[dict[str, Any]] = []
        prowlarr_error = None
        cache_key = str(app.config.prowlarr_url)
        cached = _INDEXER_CACHE.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < _INDEXER_TTL:
            indexers, prowlarr_error = cached[1], cached[2]
        else:
            try:
                from .prowlarr import ProwlarrClient

                client = ProwlarrClient.from_config(app.config)
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
                indexers.sort(
                    key=lambda item: (-item["failed"], -item["response_ms"])
                )
            except Exception as exc:
                prowlarr_error = str(exc)[:200]
            _INDEXER_CACHE[cache_key] = (
                time.monotonic(),
                indexers,
                prowlarr_error,
            )

        return {
            "timers": {
                "rss": _systemctl("catalogo-rss.timer"),
                "monitor": _systemctl("catalogo-monitor.timer"),
            },
            "last_rss": brief(latest("rss-sync")),
            "last_monitor": brief(latest("monitor")),
            "indexers": indexers[:12],
            "prowlarr_error": prowlarr_error,
        }

    def _system(self) -> dict[str, Any]:
        app = self.app
        db = app.db_path
        qbit, qbit_error = app.qbit()
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
                "media_items": conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],
                "releases": conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0],
                "torrents": conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0],
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
                        app.config.catalog_library,
                        app.config.seed_root,
                        app.config.library_root,
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
                "qbittorrent": bool(app.config.qbittorrent_pass),
                "prowlarr": bool(app.config.prowlarr_api_key),
                "jackett": bool(app.config.jackett_api_key),
                "jellyfin": bool(app.config.jellyfin_api_key),
                "radarr": bool(app.config.radarr_api_key),
                "sonarr": bool(app.config.sonarr_api_key),
                "tmdb": bool(app.config.tmdb_api_key),
                "play_service": app.play_service is not None,
            },
            "play_error": app.play_error,
            "paths": {
                "catalog_library": str(app.config.catalog_library),
                "seed_root": str(app.config.seed_root),
                "library_root": str(app.config.library_root),
                "library_root_movies": (
                    str(app.config.library_root_movies)
                    if app.config.library_root_movies
                    else ""
                ),
                "library_root_series": (
                    str(app.config.library_root_series)
                    if app.config.library_root_series
                    else ""
                ),
                "downloads_root": str(app.config.downloads_root),
                "ondemand_save_path": str(app.config.ondemand_save_path),
                "strm_roots": ":".join(app.config.strm_roots),
            },
            "auth_required": bool(app.token),
            "trackers": queries.list_trackers(db),
            "scheduler": list_tasks(db),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    # ------------------------------------------------------------- sesiones

    def _session(self, *, head: bool) -> None:
        return self._json(
            200,
            {
                "auth_required": bool(self.app.token),
                "authenticated": self._authenticated(),
            },
            head=head,
        )

    def _login(self, *, head: bool) -> None:
        if self.command != "POST":
            raise WebError(405, "usa POST")
        if not self.app.token:
            raise WebError(400, "no hay token configurado; el acceso local está abierto")
        body = self._body()
        token = str(body.get("token") or "")
        if not hmac.compare_digest(token, self.app.token):
            time.sleep(0.4)
            raise WebError(401, "token incorrecto")
        cookie = _make_cookie(self.app.token)
        self._json(
            200,
            {"ok": True},
            head=head,
            extra_headers={
                "Set-Cookie": (
                    f"{COOKIE}={cookie}; Max-Age={COOKIE_MAX_AGE}; "
                    "HttpOnly; SameSite=Strict; Path=/"
                )
            },
        )

    def _logout(self, *, head: bool) -> None:
        if self.command != "POST":
            raise WebError(405, "usa POST")
        self._body()  # consume el cuerpo para no romper keep-alive
        self._json(
            200,
            {"ok": True},
            head=head,
            extra_headers={
                "Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly"
            },
        )

    # -------------------------------------------------------------- helpers

    def _int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _release_tracker(self, raw_id: str, *, head: bool) -> None:
        try:
            release_id = int(raw_id)
        except (TypeError, ValueError):
            raise WebError(400, "release inválido")
        with closing(connect(self.app.db_path)) as conn:
            row = conn.execute(
                "SELECT r.id, r.title, r.tracker, r.info_url, r.info_hash, "
                "r.size_bytes, r.source, m.kind "
                "FROM releases r JOIN media_items m ON m.id = r.asset_id "
                "WHERE r.id = ?",
                (release_id,),
            ).fetchone()
        if row is None:
            raise WebError(404, "release no encontrado")

        info_url = str(row["info_url"] or "").strip()
        if not info_url and row["source"] == "prowlarr":
            info_url = self._find_info_url(row)
            if info_url:
                with closing(connect(self.app.db_path)) as conn:
                    conn.execute(
                        "UPDATE releases SET info_url = ? WHERE id = ?",
                        (info_url, release_id),
                    )
                    conn.commit()
        if not info_url:
            raise WebError(
                404,
                "no hay ficha del tracker para este release",
            )

        self.send_response(302)
        self.send_header("Location", info_url)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _find_info_url(self, row) -> str:
        from .importer import norm_title
        from .prowlarr import ProwlarrClient

        try:
            media_type = "series" if row["kind"] == "series" else "movie"
            results = ProwlarrClient.from_config(self.app.config).search(
                str(row["title"] or ""), media_type=media_type, limit=20
            )
        except Exception:
            return ""
        target_hash = str(row["info_hash"] or "").lower()
        target_title = norm_title(str(row["title"] or ""))
        target_size = int(row["size_bytes"] or 0)
        for item in results:
            info_url = str(item.get("infoUrl") or "").strip()
            if not info_url:
                continue
            if target_hash and str(item.get("infoHash") or "").lower() == target_hash:
                return info_url
            if norm_title(str(item.get("title") or "")) == target_title:
                if not target_size:
                    return info_url
                size = int(item.get("size") or 0)
                if size and abs(size - target_size) <= max(1, target_size * 0.02):
                    return info_url
        return ""

    def _release_torrent(self, raw_id: str, *, head: bool) -> None:
        try:
            release_id = int(raw_id)
        except (TypeError, ValueError):
            raise WebError(400, "release inválido")
        with closing(connect(self.app.db_path)) as conn:
            row = conn.execute(
                "SELECT id, title, download_url, torrent_blob "
                "FROM releases WHERE id = ?",
                (release_id,),
            ).fetchone()
        if row is None:
            raise WebError(404, "release no encontrado")

        blob = row["torrent_blob"]
        if blob:
            blob = bytes(blob)
        else:
            url = str(row["download_url"] or "").strip()
            if not url:
                raise WebError(404, "este release no tiene .torrent")
            parsed = urllib.parse.urlparse(url)
            jackett = urllib.parse.urlparse(self.app.config.jackett_url)
            if (
                jackett.netloc
                and parsed.netloc == jackett.netloc
                and "jackett_apikey=" not in url
                and self.app.config.jackett_api_key
            ):
                from .jackett import with_api_key

                url = with_api_key(url, self.app.config.jackett_api_key)
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/x-bittorrent, application/octet-stream",
                    "User-Agent": "tod-web/0.1",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    blob = response.read(50 * 1024 * 1024)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise WebError(
                    502, "no se pudo descargar el .torrent", str(exc)[:200]
                ) from exc
            if not blob.startswith(b"d"):
                raise WebError(
                    502, "el tracker no devolvió un .torrent válido"
                )

        title = re.sub(r"[^\w.\- ]+", "_", str(row["title"] or "")).strip()
        filename = (title[:120] or f"release-{release_id}") + ".torrent"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-bittorrent")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(blob)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(
        self,
        status: int,
        payload: Any,
        *,
        head: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(raw)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # Varios arranques de reproducción a la vez (varias TVs) no deben
    # quedarse en la cola de escucha.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], app: App):
        self.app = app
        super().__init__(address, Handler)

    def server_close(self) -> None:
        try:
            self.app.scheduler.stop()
            if self.app.guard is not None:
                self.app.guard.stop()
            self.app.runner.stop()
        finally:
            super().server_close()


def make_server(host: str, port: int, config: Config) -> WebServer:
    port = int(port)
    if port < 0 or port > 65535:
        raise ValueError("puerto inválido")
    app = App(config)
    return WebServer((host, port), app)


def run(host: str, port: int, config: Config) -> int:
    loopback = host in ("127.0.0.1", "localhost", "::1")
    server = make_server(host, port, config)
    if config.web_token:
        mode = "token obligatorio"
    elif loopback:
        mode = "solo loopback"
    else:
        mode = "abierto en LAN/WireGuard sin token"
    print(
        f"TOD en http://{host}:{port} db={config.db_path} "
        f"({mode}; web+reproducción+scheduler; "
        f"play_service={'sí' if server.app.play_service else 'no'})",
        flush=True,
    )
    if not loopback and not config.web_token:
        print(
            "AVISO: acceso doméstico sin login. No publiques este puerto en "
            "Internet; para fuera de casa usa WireGuard.",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.scheduler.stop()
        if server.app.guard is not None:
            server.app.guard.stop()
        server.app.runner.stop()
        server.server_close()
    return 0
