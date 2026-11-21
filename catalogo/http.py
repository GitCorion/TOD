"""HTTP mínimo y correcto para el gateway.

- /resolve?key=...  -> puntero actual (sin rutas ni secretos)
- /play?key=...     -> 200/206/304/416 si es local; 403 si es un torrent frío
- /play?key=...&download=1 -> permite arrancar la descarga de un torrent frío
- /sources?key=...  -> versiones disponibles, sin arrancar nada
- /status?key=...   -> progreso, velocidad, ETA y si se puede reproducir
- /catalog/lookup?path=... -> detecta un .strm y devuelve su estado
- /healthz          -> ok

Por defecto /play es solo lectura: un .strm de Jellyfin nunca inicia una
descarga. La app debe pedir `download=1` de forma explícita.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from contextlib import closing
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import play_intents, stream_leases
from .config import media_type
from .db import connect
from .backend import BackendError, CapacityError, NoSource, ReleaseUnavailable
from .lookup import lookup_strm
from .play_service import AdmissionDenied, NoPeers, Warming
from .ranges import parse_range
from .resolver import Resolution, Resolver
from .status import asset_status
from .stream import RangeNotSatisfiable, StreamTimeout
from .trackers import tracker_priority
from .torrent import DisabledBackend, TorrentBackend

CHUNK = 256 * 1024
# Espera máxima cuando Jellyfin arranca un prepare al darle a play.
AUTO_PREPARE_WAIT = 60.0

ADMISSION_MESSAGES = {
    "sin_espacio": "No hay espacio suficiente en disco para esta descarga.",
    "max_active": "Hay demasiadas descargas simultáneas en ese tracker.",
    "sin_peers": "La fuente no tiene peers disponibles.",
    "tracker_disabled": "Ese tracker está bloqueado.",
    "sin_politica": "No hay política de seed para ese tracker.",
    "sin_release": "No hay ninguna versión disponible.",
    "sin_tamano": "La fuente no tiene un tamaño válido.",
    "ya_en_curso": "Esa descarga ya está en curso.",
    "asset_not_found": "El título no existe en el catálogo.",
    "politica_sin_requisito": "La política del tracker no define seed mínimo.",
}


def error_message(error: str, reason: str | None = None) -> str:
    if error == "sin_fuente":
        return "No hay ninguna fuente reproducible para este título."
    if error == "admission_denied":
        if reason and reason.startswith("health_"):
            return "La fuente no está verificada como viva."
        return ADMISSION_MESSAGES.get(
            reason or "", "La descarga no cumple la política del tracker."
        )
    if error == "backend_disabled":
        return "El servicio de descargas no está activo."
    if error == "download_not_allowed":
        return "La descarga no está autorizada."
    if error == "warming":
        return "Conectando con la fuente; aún no hay datos."
    if error == "prepare_failed":
        return "No se pudo preparar la descarga."
    if error == "debrid_sin_enlace":
        return "La fuente de Debrid no tiene enlace disponible."
    return "No se pudo completar la operación."


class CatalogHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "catalogo/0.1"
    resolver: Resolver
    backend: TorrentBackend
    db_path: str
    play_intent_source: str = "range"
    play_service: Any | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/prepare", "/lease"):
            return self._text(405, "método no permitido", head=False)
        self._serve(head=False)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/lease":
            return self._text(405, "método no permitido", head=False)
        self._serve(head=False)

    def _serve(self, head: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        if parsed.path == "/healthz":
            return self._json(200, {"status": "ok"}, head=head)
        if parsed.path not in (
            "/resolve", "/play", "/sources", "/status", "/catalog/lookup",
            "/prepare", "/lease",
        ):
            return self._text(404, "no encontrado", head=head)

        if parsed.path == "/catalog/lookup":
            path = (params.get("path") or "").strip()
            if not path:
                return self._text(400, "falta path", head=head)
            qbit = (
                self.play_service.qbit_write
                if self.play_service is not None
                else None
            )
            result = lookup_strm(
                self.db_path,
                path,
                qbit=qbit,
                allowed_roots=getattr(self, "strm_roots", None),
            )
            return self._json(200, result, head=head)

        key = (params.get("key") or "").strip()
        if not key:
            return self._text(400, "falta key", head=head)

        if parsed.path == "/lease":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._json(
                    403, {"error": "lease_local_only"}, head=head
                )
            session_id = (params.get("session") or "medusa").strip()
            if self.command == "DELETE":
                removed = stream_leases.release(
                    self.db_path, key, session_id
                )
                return self._json(
                    200,
                    {
                        "released": bool(removed),
                        "key": key,
                        "session": session_id,
                    },
                    head=head,
                )
            if self.command != "POST":
                return self._text(405, "usa POST o DELETE", head=head)
            stream_leases.touch(
                self.db_path,
                key,
                session_id,
                client_ip=self.client_address[0],
                user_agent=self.headers.get("User-Agent"),
            )
            return self._json(
                200,
                {"leased": True, "key": key, "session": session_id},
                head=head,
            )

        if parsed.path == "/sources":
            return self._json(
                200, {"key": key, "sources": self.resolver.sources(key)},
                head=head,
            )

        release_id: int | None = None
        if params.get("release"):
            try:
                release_id = int(params["release"])
            except ValueError:
                return self._text(400, "release inválido", head=head)
        download_allowed = (params.get("download") or "").strip().lower() in (
            "1", "true", "yes", "on"
        )

        if parsed.path == "/prepare":
            if self.command != "POST":
                return self._text(405, "usa POST", head=head)
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._json(
                    403,
                    {"error": "prepare_local_only"},
                    head=head,
                )
            if self.play_service is None:
                return self._json(
                    503,
                    {"error": "backend_disabled", "key": key, "message": error_message("backend_disabled")},
                    head=head,
                )
            started = time.monotonic()
            runtime_raw = (params.get("runtime") or "").strip()
            if runtime_raw:
                try:
                    runtime_seconds = int(float(runtime_raw))
                except (TypeError, ValueError):
                    runtime_seconds = 0
                if 60 <= runtime_seconds <= 24 * 3600:
                    with closing(connect(self.db_path)) as conn:
                        conn.execute(
                            """
                            UPDATE media_items SET runtime_seconds = ?
                            WHERE key = ? OR media_id = ?
                            """,
                            (runtime_seconds, key, key),
                        )
                        conn.commit()

            def _prepared(status: int, payload: dict, **kwargs: Any) -> None:
                detail = payload.get("error") or payload.get("release_id")
                print(
                    f"prepare key={key} release={release_id} "
                    f"-> {status} {detail} "
                    f"in {time.monotonic() - started:.2f}s",
                    flush=True,
                )
                return self._json(status, payload, head=head, **kwargs)

            # Camino rápido: si ya está en local/archivo/debrid no hay que
            # admitir, descargar ni consultar qBittorrent. La app recibe 202
            # al instante y luego consulta /status.
            if release_id is None:
                resolution = self.resolver.resolve(key)
                if resolution.source in ("library", "local_seed", "debrid"):
                    status = asset_status(
                        self.db_path, key,
                        release_id=resolution.release_id, qbit=None,
                    )
                    return _prepared(
                        202,
                        {
                            "prepared": True,
                            "already_available": True,
                            "key": key,
                            "release_id": resolution.release_id,
                            "info_hash": resolution.info_hash,
                            "status": status,
                        },
                    )
                if resolution.source == "unavailable":
                    return _prepared(
                        503,
                        {
                            "error": "sin_fuente",
                            "message": "No hay ninguna fuente reproducible para este título.",
                            "key": key,
                            "reason": resolution.reason,
                        },
                        extra_headers={"Retry-After": "30"},
                    )

            play_intents.create(
                self.db_path,
                key,
                source="manual",
                client_ip=self.client_address[0],
                user_agent=self.headers.get("User-Agent"),
            )
            try:
                handle = self.play_service.ensure(
                    key, release_id=release_id
                )
            except AdmissionDenied as exc:
                return _prepared(
                    503,
                    {"error": "admission_denied", "message": error_message("admission_denied", exc.decision.get("reason")), **exc.decision},
                    extra_headers={"Retry-After": "30"},
                )
            except NoSource as exc:
                return _prepared(
                    503,
                    {
                        "error": "sin_fuente",
                        "message": "No hay ninguna fuente reproducible para este título.",
                        "key": key,
                        "detail": str(exc)[:200],
                        "attempts": exc.attempts[:6],
                    },
                    extra_headers={"Retry-After": "30"},
                )
            except CapacityError as exc:
                return _prepared(
                    503,
                    {
                        "error": "capacity",
                        "message": "Hay demasiadas descargas activas. Espera o cancela una.",
                        "key": key,
                        "detail": str(exc)[:200],
                    },
                    extra_headers={"Retry-After": "60"},
                )
            except ReleaseUnavailable as exc:
                return _prepared(
                    503,
                    {
                        "error": "sin_fuente",
                        "message": "No hay ninguna fuente reproducible para este título.",
                        "key": key,
                        "release_id": exc.release_id,
                        "tracker": exc.tracker,
                        "detail": str(exc)[:200],
                    },
                    extra_headers={"Retry-After": "30"},
                )
            except Exception as exc:
                return _prepared(
                    503,
                    {"error": "prepare_failed", "message": error_message("prepare_failed"), "detail": str(exc)[:200]},
                )
            # Sin qBittorrent: el estado vivo lo pide la app a /status.
            status = asset_status(
                self.db_path, key, release_id=release_id, qbit=None
            )
            return _prepared(
                202,
                {
                    "prepared": True,
                    "key": key,
                    "release_id": release_id,
                    "info_hash": handle.get("info_hash"),
                    "status": status,
                },
            )

        if parsed.path == "/status":
            qbit = (
                self.play_service.qbit_write
                if self.play_service is not None
                else None
            )
            status = asset_status(
                self.db_path, key, release_id=release_id, qbit=qbit
            )
            if status is None:
                return self._json(
                    404, {"error": "sin_estado", "key": key}, head=head
                )
            return self._json(200, status, head=head)

        resolution = (
            self.resolver.resolve_release(key, release_id)
            if release_id is not None
            else self.resolver.resolve(key)
        )
        if parsed.path == "/play" and not head and self.headers.get("Range"):
            ua = self.headers.get("User-Agent") or ""
            session_id = (
                f"{self.client_address[0]}:"
                f"{hashlib.sha1(ua.encode()).hexdigest()[:10]}"
            )
            stream_leases.touch(
                self.db_path,
                resolution.key,
                session_id,
                client_ip=self.client_address[0],
                user_agent=ua or None,
            )
        if parsed.path == "/resolve":
            return self._json(200, resolution.public(), head=head)

        if resolution.source == "debrid":
            if not resolution.debrid_url:
                return self._json(503, {"error": "debrid_sin_enlace", "message": error_message("debrid_sin_enlace")}, head=head)
            self.send_response(302)
            self.send_header("Location", resolution.debrid_url)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if resolution.source in ("library", "local_seed"):
            if not resolution.path:
                return self._text(503, "sin ruta local", head=head)
            try:
                return serve_local_file(self, resolution.path, head=head)
            except FileNotFoundError:
                return self._text(404, "fichero no disponible", head=head)

        if resolution.source == "torrent":
            prepared = self._torrent_prepared(resolution)
            auto_prepare = False
            if not prepared:
                if head:
                    return self._json(
                        503,
                        {"error": "cold_head", "key": resolution.key},
                        head=head,
                        extra_headers={"Retry-After": "5"},
                    )
                if not download_allowed:
                    # Jellyfin no llama a /prepare. Si la petición viene de
                    # una reproducción real (sesión activa con este .strm),
                    # se autoriza el arranque; un escaneo no tiene sesión.
                    auto_prepare = self._auto_prepare_allowed(
                        resolution.key, resolution
                    )
                    if not auto_prepare:
                        return self._json(
                            403,
                            {
                                "error": "download_not_allowed",
                                "message": "La descarga no está autorizada.",
                                "key": resolution.key,
                                "reason": resolution.reason,
                                "release_id": resolution.release_id,
                            },
                            head=head,
                            extra_headers={"X-Catalogo-Download": "disabled"},
                        )
                if not self.headers.get("Range"):
                    return self._json(
                        503,
                        {"error": "cold_no_range", "key": resolution.key},
                        head=head,
                        extra_headers={"Retry-After": "5"},
                    )
            if self.play_service is None:
                intent = play_intents.create(
                    self.db_path,
                    resolution.key,
                    source=self.play_intent_source,
                    client_ip=self.client_address[0],
                    user_agent=self.headers.get("User-Agent"),
                )
                return self._json(
                    503,
                    {
                        "error": "backend_disabled",
                        "message": "El servicio de descargas no está activo.",
                        "key": resolution.key,
                        "reason": resolution.reason,
                        "release_id": resolution.release_id,
                        "file_index": resolution.file_index,
                        "play_intent_id": intent["id"],
                    },
                    head=head,
                    extra_headers={"Retry-After": "5"},
                )
            candidates = self._candidate_release_ids(
                resolution.key, release_id
            )
            if not candidates and release_id is not None:
                failed = (
                    self.play_service is not None
                    and self.play_service.is_failed(release_id)
                )
                if not failed:
                    candidates = [release_id]
            errors: list[dict[str, Any]] = []
            warming_error: dict[str, Any] | None = None
            for candidate_id in candidates:
                try:
                    candidate = self.resolver.resolve_release(
                        resolution.key, candidate_id
                    )
                except Exception as exc:
                    errors.append(
                        {"release_id": candidate_id, "error": str(exc)[:200]}
                    )
                    continue
                if candidate.source == "debrid":
                    if not candidate.debrid_url:
                        errors.append(
                            {"release_id": candidate_id,
                             "error": "debrid_sin_enlace"}
                        )
                        continue
                    self.send_response(302)
                    self.send_header("Location", candidate.debrid_url)
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return None
                if candidate.source in ("library", "local_seed"):
                    if not candidate.path:
                        errors.append(
                            {"release_id": candidate_id,
                             "error": "sin_ruta_local"}
                        )
                        continue
                    try:
                        return serve_local_file(
                            self, candidate.path, head=head
                        )
                    except FileNotFoundError:
                        errors.append(
                            {"release_id": candidate_id,
                             "error": "fichero_no_disponible"}
                        )
                        continue
                if candidate.source != "torrent":
                    errors.append(
                        {"release_id": candidate_id,
                         "error": f"source_{candidate.source}"}
                    )
                    continue
                start, end = self._parse_play_range(candidate)
                if start is None:
                    errors.append(
                        {"release_id": candidate_id,
                         "error": "range_invalid"}
                    )
                    continue
                play_intents.create(
                    self.db_path,
                    resolution.key,
                    source=self.play_intent_source,
                    client_ip=self.client_address[0],
                    user_agent=self.headers.get("User-Agent"),
                )
                handle = None
                try:
                    handle = self.play_service.ensure(
                        resolution.key, release_id=candidate_id
                    )
                    if auto_prepare:
                        print(
                            f"auto_prepare key={resolution.key} "
                            f"release={candidate_id}",
                            flush=True,
                        )
                        if not self.play_service.wait_stream_ready(
                            handle, timeout=AUTO_PREPARE_WAIT
                        ):
                            return self._json(
                                503,
                                {
                                    "error": "auto_prepare_waiting",
                                    "message": (
                                        "Preparando la fuente; vuelve a "
                                        "intentarlo en unos segundos."
                                    ),
                                    "key": resolution.key,
                                    "release_id": candidate_id,
                                },
                                head=head,
                                extra_headers={"Retry-After": "10"},
                            )
                    else:
                        self.play_service.preflight(handle)
                    self.play_service.stream_prepared(
                        self, handle, start, end
                    )
                    return None
                except RangeNotSatisfiable:
                    errors.append(
                        {"release_id": candidate_id,
                         "error": "range_invalid"}
                    )
                except AdmissionDenied as exc:
                    errors.append(
                        {"release_id": candidate_id,
                         "error": "admission_denied",
                         **exc.decision}
                    )
                except Warming as exc:
                    # Tiene pares pero aún no hay datos: no se pausa. Se
                    # apunta y se prueba otro candidato ya disponible.
                    warming_error = warming_error or {
                        "error": "warming",
                        "message": "Conectando con la fuente; aún no hay datos.",
                        "key": resolution.key,
                        "release_id": candidate_id,
                        "detail": str(exc)[:200],
                    }
                    errors.append(
                        {
                            "release_id": candidate_id,
                            "error": "warming",
                            "detail": str(exc)[:200],
                        }
                    )
                    continue
                except StreamTimeout as exc:
                    warming_error = warming_error or {
                        "error": "warming",
                        "message": "Conectando con la fuente; aún no hay datos.",
                        "key": resolution.key,
                        "release_id": candidate_id,
                        "detail": str(exc)[:200],
                    }
                    errors.append(
                        {
                            "release_id": candidate_id,
                            "error": "stream_timeout",
                            "detail": str(exc)[:200],
                        }
                    )
                    continue
                except (NoPeers, BackendError) as exc:
                    if handle is not None:
                        self.play_service.discard_failed(handle)
                    errors.append(
                        {
                            "release_id": candidate_id,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200],
                        }
                    )
            if warming_error is not None:
                return self._json(
                    503,
                    warming_error,
                    head=head,
                    extra_headers={"Retry-After": "5"},
                )
            return self._json(
                503,
                {
                    "error": "sin_fuente",
                    "message": "No hay ninguna fuente reproducible para este título.",
                    "key": resolution.key,
                    "reason": "all_sources_failed",
                    "attempts": errors[:6],
                },
                head=head,
                extra_headers={"Retry-After": "30"},
            )

        return self._json(
            404,
            {"error": "sin_fuente", "message": "No hay ninguna fuente reproducible para este título.",
             "key": resolution.key, "reason": resolution.reason},
            head=head,
        )

    def _candidate_release_ids(
        self, key: str, requested: int | None
    ) -> list[int]:
        try:
            sources = self.resolver.sources(key)
        except Exception:
            sources = []
        requested_source = next(
            (
                source for source in sources
                if int(source.get("release_id") or 0) == requested
            ),
            None,
        )

        def rank(source: dict) -> tuple:
            same_audio = 0 if (
                requested_source
                and source.get("audio") == requested_source.get("audio")
            ) else 1
            health = {"alive": 0, "unknown": 1, "stale": 2}.get(
                source.get("health"), 3
            )
            return (
                tracker_priority(source.get("tracker")),
                health,
                int(source.get("size_bytes") or 0),
                -int(source.get("seeders") or 0),
                same_audio,
            )

        ordered: list[int] = []
        for source in sorted(sources, key=rank):
            release_id = source.get("release_id")
            if release_id is None:
                continue
            release_id = int(release_id)
            if release_id in ordered:
                continue
            if self.play_service is not None and self.play_service.is_failed(
                release_id
            ):
                continue
            ordered.append(release_id)
        if requested is not None:
            failed = (
                self.play_service is not None
                and self.play_service.is_failed(requested)
            )
            if not failed:
                if requested in ordered:
                    ordered.remove(requested)
                ordered.insert(0, int(requested))

        def availability(release_id: int) -> int:
            try:
                resolution = self.resolver.resolve_release(key, release_id)
            except Exception:
                return 2
            if resolution.source in ("library", "local_seed", "debrid"):
                return 0
            if resolution.source == "torrent":
                return 1
            return 2

        # Servir primero lo que ya está disponible; si el release pedido
        # todavía está frío, no bloquear una versión ya descargada.
        ordered.sort(key=availability)
        return ordered[:4]

    def _auto_prepare_allowed(
        self, key: str, resolution: Resolution
    ) -> bool:
        """¿Esta petición viene de una reproducción real en Jellyfin?

        Por defecto no: el servicio base no conoce Jellyfin. La web lo
        sobrescribe consultando las sesiones activas.
        """
        return False

    def _torrent_prepared(self, resolution: Resolution) -> bool:
        """¿La app ya pidió Prepare para este título?

        Un `.strm` frío sin prepare sigue devolviendo 403. Si ya hay un
        compromiso/torrent (o un lease reciente), se permite streaming
        progresivo aunque la URL diga `download=0`.
        """
        if resolution.asset_id is None:
            return False
        try:
            with closing(connect(self.db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT 1 FROM seed_commitments
                    WHERE asset_id = ?
                      AND state IN ('approved','active','seeding')
                      AND (
                          ? IS NULL OR release_id IS NULL OR release_id = ?
                      )
                    LIMIT 1
                    """,
                    (
                        int(resolution.asset_id),
                        resolution.release_id,
                        resolution.release_id,
                    ),
                ).fetchone()
                if row is not None:
                    return True
                row = conn.execute(
                    """
                    SELECT 1
                    FROM torrents t
                    JOIN releases r ON r.id = t.release_id
                    WHERE r.asset_id = ? AND t.category = 'ondemand'
                    LIMIT 1
                    """,
                    (int(resolution.asset_id),),
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def _parse_play_range(
        self, resolution: Resolution
    ) -> tuple[int | None, int | None]:
        header = self.headers.get("Range")
        if not header:
            return 0, None
        size = resolution.size or 0
        if size > 0:
            result = parse_range(header, size)
            if result.status == 416:
                return None, None
            if result.status == 206:
                return result.start or 0, result.end
            return 0, None
        match = re.match(r"bytes=(\d*)-(\d*)", header)
        if not match or (not match.group(1) and not match.group(2)):
            return None, None
        start = int(match.group(1)) if match.group(1) else None
        end = int(match.group(2)) if match.group(2) else None
        if start is None:
            return None, None
        return start, end

    def _text(self, status: int, message: str, *, head: bool) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(
        self,
        status: int,
        obj: Any,
        *,
        head: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # La app cerró la conexión tras su timeout; no es un fallo
                # del proxy, solo un cliente que ya no escucha.
                pass


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    values = [value.strip() for value in header.split(",")]
    return "*" in values or etag in values or etag.strip('"') in [
        value.strip('"') for value in values
    ]


def serve_local_file(handler: BaseHTTPRequestHandler, path: str, *,
                     head: bool = False) -> None:
    file_path = Path(path)
    stat = file_path.stat()
    size = stat.st_size
    etag = f'"{size:x}-{stat.st_mtime_ns:x}"'
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    mime = media_type(str(file_path))

    if _etag_matches(handler.headers.get("If-None-Match"), etag):
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Last-Modified", last_modified)
        handler.send_header("Cache-Control", "private, max-age=0, must-revalidate")
        handler.end_headers()
        return

    result = parse_range(handler.headers.get("Range"), size)
    if result.status == 416:
        handler.send_response(416)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Range", result.content_range or f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.send_header("ETag", etag)
        handler.send_header("Last-Modified", last_modified)
        handler.end_headers()
        return

    start = result.start or 0
    end = size - 1 if result.end is None else result.end
    length = max(0, end - start + 1) if size else 0

    handler.send_response(result.status)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if result.status == 206:
        handler.send_header("Content-Range", result.content_range or "")
    handler.send_header("ETag", etag)
    handler.send_header("Last-Modified", last_modified)
    handler.send_header("Cache-Control", "private, max-age=0, must-revalidate")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()

    if head or length == 0:
        return

    with file_path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(CHUNK, remaining))
            if not data:
                break
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                break
            remaining -= len(data)


def make_server(
    host: str,
    port: int,
    db_path: str | os.PathLike[str],
    *,
    backend: TorrentBackend | None = None,
    play_service: Any | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundCatalogHandler",
        (CatalogHandler,),
        {
            "resolver": Resolver(db_path),
            "backend": backend or DisabledBackend(),
            "db_path": str(db_path),
            "play_service": play_service,
        },
    )
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 128

    return Server((host, port), handler)

