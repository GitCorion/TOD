"""Servicio de reproducción on-demand: admisión + prepare + stream."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from . import play_intents
from .admission import admit
from .backend import (
    DEFAULT_SAVE_PATH,
    BackendError,
    CapacityError,
    NoPolicy,
    NoSeedCommitment,
    NoSource,
    QbitWrite,
    ReleaseUnavailable,
    prepare_download,
)
from .qbittorrent import QbittorrentError
from .resolver import Resolver
from .stream import DEFAULT_TIMEOUT, QbitRead, stream_torrent_file

MAX_FALLBACK_CANDIDATES = 5

DEFAULT_DATA_PATH = "/mnt/descargas"
DEFAULT_RESERVE_BYTES = 20 * 1024**3
PREFLIGHT_TIMEOUT = 15.0
NO_PEERS_GRACE = 3.0
FAIL_COOLDOWN = 300.0


class AdmissionDenied(BackendError):
    def __init__(self, decision: dict[str, Any]):
        super().__init__(decision.get("reason", "admission_denied"))
        self.decision = decision


class NoPeers(BackendError):
    """El torrent se añadió pero no consiguió pares/datos a tiempo."""


class Warming(BackendError):
    """El torrent tiene pares pero aún no hay datos; se deja descargando."""


class EngineUnavailable(BackendError):
    """qBittorrent no responde o perdió el torrent al reiniciarse."""


class PlayService:
    def __init__(
        self,
        db_path: str,
        qbit_read: QbitRead,
        qbit_write: QbitWrite,
        *,
        data_path: str = DEFAULT_DATA_PATH,
        save_path: str = DEFAULT_SAVE_PATH,
        reserve_bytes: int = DEFAULT_RESERVE_BYTES,
        jackett_api_key: str | None = None,
        max_active: int = 2,
        max_size_bytes: int = 0,
        tmdb_api_key: str | None = None,
        stream_roots: list[str] | None = None,
    ):
        self.db_path = db_path
        self.qbit_read = qbit_read
        self.qbit_write = qbit_write
        self.data_path = data_path
        self.save_path = save_path
        self.reserve_bytes = reserve_bytes
        self.jackett_api_key = jackett_api_key
        self.max_active = max_active
        self.max_size_bytes = max_size_bytes
        self.tmdb_api_key = tmdb_api_key
        self.stream_roots = list(stream_roots or [])
        self._failed: dict[int, float] = {}

    def _live_torrent(self, info_hash: str) -> dict[str, Any] | None:
        direct = getattr(self.qbit_write, "torrent", None)
        if callable(direct):
            try:
                found = direct(info_hash)
            except Exception:
                found = None
            if found is not None:
                return found
        try:
            torrents = self.qbit_write.torrents()
        except Exception:
            return None
        for torrent in torrents:
            if (torrent.get("hash") or "").lower() == info_hash.lower():
                return torrent
        return None

    def is_failed(self, release_id: int | None) -> bool:
        if release_id is None:
            return False
        until = self._failed.get(int(release_id))
        return until is not None and until > time.monotonic()

    def mark_failed(self, release_id: int | None) -> None:
        if release_id is not None:
            self._failed[int(release_id)] = time.monotonic() + FAIL_COOLDOWN

    def discard_failed(self, handle: dict[str, Any]) -> None:
        """Libera un candidato fallido.

        Un torrent que no llegó a descargar nada no debe ocupar una plaza de
        admisión ni dejar un compromiso de seed. Si ya tenía datos, se pausa
        (privado) o se borra (público con muy poco progreso).
        """
        info_hash = handle.get("info_hash") or ""
        release_id = handle.get("release_id")
        self.mark_failed(release_id)
        if not info_hash:
            return
        try:
            # TOD no borra torrents ni ficheros. Un candidato fallido se
            # pausa para no ocupar la admisión; qBittorrent decide después
            # si lo elimina con sus propias reglas.
            self.qbit_write.pause(info_hash)
            self._forget_failed(info_hash, release_id)
        except Exception:
            pass

    def _forget_failed(self, info_hash: str, release_id: int | None) -> None:
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE seed_commitments
                SET state = 'cancelled', reason = 'play_failed',
                    completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE state IN ('approved', 'active')
                  AND (
                      (? IS NOT NULL AND release_id = ?)
                      OR info_hash = ?
                  )
                """,
                (release_id, release_id, info_hash),
            )
            conn.execute(
                "UPDATE torrents SET state='paused', qbit_state='pausedDL' "
                "WHERE info_hash = ?",
                (info_hash,),
            )
            conn.commit()

    def preflight(
        self, handle: dict[str, Any], *, timeout: float = PREFLIGHT_TIMEOUT
    ) -> None:
        """Espera datos reales; si no hay pares, falla rápido para el fallback."""
        info_hash = handle.get("info_hash") or ""
        deadline = time.monotonic() + max(1.0, timeout)
        no_peers_since: float | None = None
        had_peers = False
        while True:
            torrent = self._live_torrent(info_hash)
            if torrent is None:
                raise NoPeers("torrent no encontrado")
            state = (torrent.get("state") or "").lower()
            if "error" in state or "missing" in state:
                raise NoPeers(f"estado {state}")
            progress = float(torrent.get("progress") or 0)
            speed = int(torrent.get("dlspeed") or 0)
            if progress > 0 or speed > 0:
                return
            if state in ("uploading", "stalledup", "seeding", "complete"):
                return
            complete = int(torrent.get("num_complete") or 0)
            seeds = int(torrent.get("num_seeds") or 0)
            if complete > 0 or seeds > 0:
                had_peers = True
            now = time.monotonic()
            if complete == 0 and seeds == 0:
                if no_peers_since is None:
                    no_peers_since = now
                elif now - no_peers_since >= NO_PEERS_GRACE:
                    raise NoPeers("sin pares en el tracker")
            else:
                no_peers_since = None
            if now >= deadline:
                if had_peers:
                    raise Warming("conectando, aún sin datos")
                raise NoPeers("sin datos a tiempo")
            time.sleep(0.5)

    def _candidates(
        self, media_id: str, requested: int | None
    ) -> list[int]:
        """Candidatos ordenados: salud, tamaño ascendente y seeds."""
        try:
            sources = Resolver(self.db_path).sources(media_id)
        except Exception:
            sources = []
        ordered: list[int] = []
        for source in sources:
            candidate = source.get("release_id")
            if candidate is None:
                continue
            candidate = int(candidate)
            if candidate in ordered:
                continue
            if self.is_failed(candidate):
                continue
            ordered.append(candidate)
        if requested is not None and int(requested) in ordered:
            ordered.remove(int(requested))
            ordered.insert(0, int(requested))
        elif requested is not None and not ordered:
            ordered.insert(0, int(requested))
        return ordered[:MAX_FALLBACK_CANDIDATES]

    def _has_commitment(self, media_id: str, release_id: int | None) -> bool:
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM seed_commitments
                WHERE asset_id = (
                    SELECT id FROM media_items WHERE media_id=? OR key=? LIMIT 1
                )
                  AND state IN ('approved','active','seeding')
                  AND (? IS NULL OR release_id = ?)
                LIMIT 1
                """,
                (media_id, media_id, release_id, release_id),
            ).fetchone()
        return row is not None

    def _cancel_approved(self, release_id: int, reason: str) -> None:
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE seed_commitments
                SET state = 'cancelled', reason = ?,
                    completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE release_id = ? AND state = 'approved'
                  AND NOT EXISTS (
                      SELECT 1 FROM torrents t WHERE t.release_id = ?
                  )
                """,
                (reason, int(release_id), int(release_id)),
            )
            conn.commit()

    def ensure_runtime(self, media_id: str) -> int | None:
        """Rellena la duración desde TMDB si Jellyfin no la tiene."""
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, kind, tmdb, season, episode, runtime_seconds "
                "FROM media_items WHERE media_id = ? OR key = ? LIMIT 1",
                (media_id, media_id),
            ).fetchone()
        if row is None:
            return None
        if row["runtime_seconds"]:
            return int(row["runtime_seconds"])
        if not row["tmdb"] or not self.tmdb_api_key:
            return None
        try:
            from .tmdb import TmdbClient

            client = TmdbClient(self.tmdb_api_key)
            minutes = 0
            if row["kind"] == "movie":
                details = client.movie_details(int(row["tmdb"]))
                minutes = int(details.get("runtime") or 0)
            elif row["kind"] == "series" and row["season"] is not None:
                season = int(row["season"] or 0)
                episode = int(row["episode"] or 0)
                details = client.season_details(int(row["tmdb"]), season)
                for item in details.get("episodes") or []:
                    if int(item.get("episode_number") or 0) == episode:
                        minutes = int(item.get("runtime") or 0)
                        break
                if minutes <= 0:
                    series = client.tv_details(int(row["tmdb"]))
                    runtimes = series.get("episode_run_time") or []
                    if runtimes:
                        minutes = int(runtimes[0] or 0)
        except Exception as exc:
            print(
                f"runtime TMDB {media_id}: {str(exc)[:160]}", flush=True
            )
            return None
        if minutes <= 0:
            return None
        seconds = minutes * 60
        with closing(connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE media_items SET runtime_seconds = ? WHERE id = ?",
                (seconds, int(row["id"])),
            )
            conn.commit()
        return seconds

    def _live_download_hashes(self) -> set[str] | None:
        """Hashes que qBittorrent está descargando de verdad ahora."""
        try:
            torrents = self.qbit_write.torrents()
        except Exception:
            return None
        active = {"downloading", "forceddl", "stalleddl"}
        return {
            (item.get("hash") or "").lower()
            for item in torrents
            if (item.get("state") or "").lower() in active
            and item.get("hash")
        }

    def _tracker_for_release(self, release_id: int) -> str | None:
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT tracker FROM releases WHERE id = ?",
                (int(release_id),),
            ).fetchone()
        return row["tracker"] if row else None

    def _active_for_tracker_live(
        self, tracker: str, live_hashes: set[str]
    ) -> int:
        from contextlib import closing

        from .db import connect

        with closing(connect(self.db_path)) as conn:
            pending = conn.execute(
                """
                SELECT COUNT(*) FROM seed_commitments i
                WHERE i.tracker = ?
                  AND i.state = 'approved'
                  AND NOT EXISTS (
                      SELECT 1 FROM torrents t
                      WHERE (
                          i.info_hash IS NOT NULL
                          AND t.info_hash = i.info_hash
                      ) OR (
                          i.release_id IS NOT NULL
                          AND t.release_id = i.release_id
                      )
                  )
                """,
                (tracker,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT t.info_hash FROM torrents t
                LEFT JOIN releases r ON r.id = t.release_id
                WHERE COALESCE(r.tracker, t.tracker) = ?
                  AND t.category = 'ondemand'
                  AND t.state IN ('waiting', 'downloading')
                """,
                (tracker,),
            ).fetchall()
        active = sum(
            1 for row in rows
            if (row["info_hash"] or "").lower() in live_hashes
        )
        return int(pending) + active

    def ensure(
        self,
        media_id: str,
        *,
        release_id: int | None = None,
        torrent_bytes: bytes | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        intent = play_intents.active(self.db_path, media_id)
        if intent is None:
            raise BackendError("no hay play_intent activo")
        try:
            self.ensure_runtime(media_id)
        except Exception:
            pass

        from .settings import ondemand_max_active

        max_active = ondemand_max_active(self.db_path, self.max_active)

        # Con bytes explícitos no se inventan candidatos: el llamante ya
        # decidió el torrent exacto.
        if torrent_bytes is not None:
            candidates: list[int | None] = [release_id]
        else:
            candidates = list(self._candidates(media_id, release_id))
            if not candidates:
                candidates = [release_id]

        attempts: list[dict[str, Any]] = []
        live_hashes = self._live_download_hashes()
        active_cache: dict[str, int] = {}
        for candidate in candidates:
            if candidate is not None and self.is_failed(candidate):
                continue
            if not self._has_commitment(media_id, candidate):
                tracker = (
                    self._tracker_for_release(candidate)
                    if candidate is not None
                    else None
                )
                active_override = None
                if tracker is not None and live_hashes is not None:
                    if tracker not in active_cache:
                        active_cache[tracker] = self._active_for_tracker_live(
                            tracker, live_hashes
                        )
                    active_override = active_cache[tracker]
                result = admit(
                    self.db_path,
                    media_id,
                    release_id=candidate,
                    data_path=self.data_path,
                    reserve_bytes=self.reserve_bytes,
                    # La salud "alive" ordena candidatos, pero no bloquea:
                    # muchos releases de catálogo aún están en "unknown".
                    # El .torrent se valida antes de añadirlo y, si está
                    # roto, se marca dead y se prueba el siguiente.
                    require_health=False,
                    min_seeders=1,
                    max_size_bytes=self.max_size_bytes,
                    active_for_tracker_override=active_override,
                )
                if not result.decision.allowed:
                    attempts.append(
                        {
                            "release_id": candidate,
                            "tracker": result.tracker or None,
                            "error": "admission_denied",
                            "reason": result.decision.reason,
                        }
                    )
                    if result.decision.reason == "asset_not_found":
                        break
                    continue
                candidate = result.release_id
            try:
                return prepare_download(
                    self.db_path,
                    media_id,
                    self.qbit_write,
                    release_id=candidate,
                    torrent_bytes=torrent_bytes,
                    jackett_api_key=self.jackett_api_key,
                    save_path=self.save_path,
                    max_active=max_active,
                    force=force,
                )
            except CapacityError:
                raise
            except QbittorrentError as exc:
                # El motor no responde: no es culpa de esta fuente.
                raise EngineUnavailable(str(exc)[:200]) from exc
            except ReleaseUnavailable as exc:
                if candidate is not None:
                    self.mark_failed(candidate)
                    if not exc.permanent:
                        self._cancel_approved(candidate, "fetch_transitorio")
                attempts.append(
                    {
                        "release_id": exc.release_id or candidate,
                        "tracker": exc.tracker,
                        "error": "release_unavailable",
                        "detail": str(exc)[:200],
                        "permanent": exc.permanent,
                    }
                )
                continue
            except (NoPolicy, NoSeedCommitment, BackendError) as exc:
                if candidate is not None:
                    self._cancel_approved(candidate, "prepare_failed")
                attempts.append(
                    {
                        "release_id": candidate,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:200],
                    }
                )
                continue

        raise NoSource("sin fuente disponible", attempts=attempts)

    def wait_stream_ready(
        self,
        handle: dict[str, Any],
        *,
        timeout: float = 60.0,
        interval: float = 2.0,
    ) -> bool:
        """Espera a que haya buffer contiguo y cola lista.

        Para un `prepare` lanzado desde Jellyfin: en vez de fallar al instante,
        se sostiene la petición mientras el torrent arranca.
        """
        from .status import _buffer_state

        info_hash = (handle.get("info_hash") or "").lower()
        file_index = handle.get("file_index")
        if not info_hash or file_index is None:
            return False
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            live = self._live_torrent(info_hash)
            if live is not None and float(live.get("progress") or 0) >= 0.999:
                return True
            try:
                files = self.qbit_read.files(info_hash)
            except Exception:
                files = []
            file_size = None
            for item in files:
                if int(item.get("index") or 0) == int(file_index):
                    file_size = int(item.get("size") or 0) or None
                    break
            buffer_bytes, required, tail_ready = _buffer_state(
                self.qbit_read, info_hash, files, int(file_index), file_size
            )
            if (
                buffer_bytes is not None
                and required > 0
                and buffer_bytes >= required
                and tail_ready
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.2, interval))

    def stream_prepared(
        self,
        handler: Any,
        handle: dict[str, Any],
        start: int,
        end: int | None,
        *,
        head: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        intent_id: int | None = None,
    ) -> None:
        on_first_byte = None
        if intent_id is not None:
            from . import play_intents

            def mark_first_byte() -> None:
                try:
                    play_intents.first_byte(self.db_path, intent_id)
                except Exception:
                    pass

            on_first_byte = mark_first_byte
            try:
                play_intents.ready(self.db_path, intent_id)
            except Exception:
                pass
        roots = list(self.stream_roots)
        save_path = handle.get("save_path")
        if save_path:
            roots.insert(0, str(save_path))
        stream_torrent_file(
            handler,
            self.qbit_read,
            handle["info_hash"],
            handle["file_index"],
            start,
            end,
            roots=roots or None,
            head=head,
            timeout=timeout,
            on_first_byte=on_first_byte,
        )

    def stream(
        self,
        handler: Any,
        media_id: str,
        start: int,
        end: int | None,
        *,
        head: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        release_id: int | None = None,
        torrent_bytes: bytes | None = None,
        intent_id: int | None = None,
    ) -> dict[str, Any]:
        handle = self.ensure(
            media_id, release_id=release_id, torrent_bytes=torrent_bytes
        )
        self.stream_prepared(
            handler, handle, start, end, head=head, timeout=timeout,
            intent_id=intent_id,
        )
        return handle
