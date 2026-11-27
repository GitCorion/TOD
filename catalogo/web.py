"""Panel web, API, reproducción y scheduler en un único proceso.

- API JSON + SPA sin dependencias externas.
- Las mutaciones exigen la cabecera `X-Catalogo: web` (anti-CSRF) y, si se
  configura `CATALOGO_WEB_TOKEN`, una sesión válida.
- Nunca expone `download_url`, claves de API ni enlaces de debrid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import gzip
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import (
    app_settings,
    admin,
    attention,
    integrations,
    intake,
    jellyfin,
    jobs,
    ondemand,
    queries,
)
from . import (
    api_dashboard,
    api_downloads,
    api_intake,
    api_jobs,
    api_library,
    api_monitor,
    api_ondemand,
    api_preferences,
    api_releases,
    api_settings,
    api_status,
    api_titles,
    api_tmdb,
    language_settings,
    policy,
    seed_settings,
    settings,
    strm_inventory,
)
from . import strm as strm_module
from .arr_data import ArrData
from .config import Config
from .db import connect, init_db
from .errors import WebError
from .http import CatalogHandler as PlaybackHandler
from .jobs import JobConflict, JobError
from .log import get_logger
from .qbittorrent import QbittorrentClient, QbittorrentError
from .qbit_write import QbittorrentWriteClient
from .resolver import Resolver
from .scheduler import Scheduler, list_tasks, save_task
from .stream_guard import StreamGuard

LOG = get_logger("web")

STATIC_DIR = Path(__file__).resolve().parent / "web_static"
COOKIE = "catalogo_web"
COOKIE_MAX_AGE = 7 * 24 * 3600
MAX_BODY = 1_000_000
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
        threading.Thread(
            target=self._warm_inventory, daemon=True, name="tod-warm"
        ).start()

    def _warm_inventory(self) -> None:
        """Primer recorrido de los `.strm` fuera del camino de una petición."""
        try:
            strm_inventory.inventory(self.config.catalog_library)
        except Exception:
            LOG.debug("no se pudo precalentar el inventario", exc_info=True)

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
                f"añadiendo a Jellyfin {index + 1}/{len(keys)}",
                progress=0.5 + 0.5 * (index / max(1, len(keys))),
            )
            report = admin.export_strm(
                self.db_path,
                self.config.catalog_library,
                base_url=self.config.playback_base_url,
                key=key,
                limit=None,
                versions_filter="alive",
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
        from .strm_inventory import invalidate

        written = 0
        to_notify: list[str] = []
        for key in keys:
            try:
                report = admin.export_strm(
                    self.db_path,
                    self.config.catalog_library,
                    base_url=self.config.playback_base_url,
                    key=key,
                    limit=None,
                    versions_filter="alive",
                    skip_library=False,
                )
                written += int(report.get("written") or 0)
                to_notify.extend(report.get("written_paths") or [])
            except Exception:
                pass
        if to_notify:
            invalidate(self.config.catalog_library)
            jellyfin.notify_updated(self.config, to_notify[:500])
        return {
            "requested": len(keys),
            "downloads": results,
            "download": results[0]["download"] if results else None,
            "sync": results[0]["sync"] if results else None,
            "published": written,
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
    "/media-info",
    "/catalog/lookup",
    "/prepare",
    "/lease",
}


class Handler(PlaybackHandler):
    server_version = "tod/0.3"
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
        LOG.info("%s %s", self.command, self.path.split("?", 1)[0])

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
            if path.startswith("/trailarr/"):
                return self._trailarr_bridge(path, params, head=head)
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
            self._api(parts, params, body, head=head, mutate=mutate)
            if mutate:
                ondemand.invalidate_caches()
            return
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
            LOG.exception("error atendiendo %s %s", self.command, path)
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

    def _trailarr_bridge(
        self, path: str, params: dict[str, str], *, head: bool
    ) -> None:
        from . import trailarr as trailarr_mod

        token = self.app.token
        if token and not self._authenticated():
            provided = str(
                self.headers.get("X-Api-Key") or params.get("apikey") or ""
            )
            if not provided or not hmac.compare_digest(provided, token):
                raise WebError(401, "API key inválida")

        db = self.app.db_path
        cfg = self.app.config
        clean = path.rstrip("/")
        if clean == "/trailarr/radarr/api/v3/system/status":
            return self._json(200, trailarr_mod.radarr_system_status(), head=head)
        if clean == "/trailarr/radarr/api/v3/rootfolder":
            return self._json(200, trailarr_mod.radarr_root_folders(db, cfg), head=head)
        if clean == "/trailarr/radarr/api/v3/movie":
            return self._json(200, trailarr_mod.radarr_movies(db, cfg), head=head)
        if clean in (
            "/trailarr/radarr/api/v3/tag",
            "/trailarr/radarr/api/v3/qualityprofile",
        ):
            return self._json(200, [], head=head)
        if clean == "/trailarr/sonarr/api/v3/system/status":
            return self._json(200, trailarr_mod.sonarr_system_status(), head=head)
        if clean == "/trailarr/sonarr/api/v3/rootfolder":
            return self._json(200, trailarr_mod.sonarr_root_folders(db, cfg), head=head)
        if clean == "/trailarr/sonarr/api/v3/series":
            return self._json(200, trailarr_mod.sonarr_series(db, cfg), head=head)
        if clean in (
            "/trailarr/sonarr/api/v3/tag",
            "/trailarr/sonarr/api/v3/qualityprofile",
            "/trailarr/sonarr/api/v3/languageprofile",
        ):
            return self._json(200, [], head=head)
        LOG.warning("trailarr bridge 404: %s", path)
        raise WebError(404, "no encontrado")

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

        if parts and len(parts) == 1 and parts[0] in (
            "overview", "events", "calendar", "activity", "search"
        ):
            result = api_dashboard.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] in ("collections", "series"):
            result = api_library.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "intake":
            result = api_intake.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "ondemand":
            result = api_ondemand.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts == ["trackers"] and not mutate:
            return self._json(200, {"items": queries.list_trackers(db)}, head=head)

        if parts and parts[0] == "titles":
            result = api_titles.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "tmdb":
            result = api_tmdb.handle(app, parts, params)
            status_code, payload = result
            return self._json(status_code, payload, head=head)

        if parts and parts[0] == "releases":
            result = api_releases.handle(
                app,
                parts,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] in ("downloads", "blocklist"):
            result = api_downloads.handle(
                app, parts, params, body, mutate=mutate
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] in ("language", "seed", "search-fallback", "profiles", "custom-formats", "restrictions", "naming", "root-folders", "tags", "media-tags", "custom-filters", "import-lists"):
            result = api_preferences.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] in ("seeds", "indexers", "monitor"):
            result = api_monitor.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts and parts[0] == "jobs":
            result = api_jobs.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        if parts == ["setup"] and not mutate:
            return self._json(200, api_status.setup_status(app.config, app.db_path), head=head)

        if parts and parts[0] in (
            "settings", "scheduler", "attention", "setup", "system",
            "trackers", "integrations"
        ):
            result = api_settings.handle(
                app,
                parts,
                params,
                body,
                client_ip=self.client_address[0],
                mutate=mutate,
            )
            if result is not None:
                status_code, payload = result
                return self._json(status_code, payload, head=head)
            raise WebError(404, "no encontrado")

        raise WebError(404, "no encontrado")

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
    LOG.info(
        "TOD en http://%s:%s db=%s (%s; web+reproducción+scheduler; play_service=%s)",
        host, port, config.db_path, mode,
        "sí" if server.app.play_service else "no",
    )
    if not loopback and not config.web_token:
        LOG.warning(
            "acceso doméstico sin login; no publiques este puerto en Internet, "
            "para fuera de casa usa WireGuard"
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
