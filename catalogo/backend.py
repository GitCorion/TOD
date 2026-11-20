"""Backend de escritura: intención de play + seed_commitment + qBittorrent.

No se llama a `torrents/add` sin las dos cosas. El torrent se añade pausado;
solo el play lo reanuda y fuerza.
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .db import connect
from .jackett import with_api_key
from .policy import get_policy
from .qbittorrent import QbittorrentError
from .qbit_sync import VIDEO_EXT, _episode_from_name, _storage_for
from .torrent_meta import info_dict_bytes

DEFAULT_SAVE_PATH = "/mnt/descargas/ondemand"
DEFAULT_CATEGORY = "ondemand"
MAX_ACTIVE_ONDEMAND = 2


class BackendError(RuntimeError):
    pass


class TorrentFetchError(BackendError):
    """No se pudo obtener un `.torrent` válido del índice.

    ``permanent`` distingue un release retirado/roto (404, login, HTML) de
    un fallo temporal (429, timeout, 5xx sin pistas).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        permanent: bool = False,
        body: bytes = b"",
    ):
        super().__init__(message)
        self.status = status
        self.permanent = permanent
        self.body = body[:512]


class ReleaseUnavailable(BackendError):
    """El release no se puede descargar; puede haber otro candidato."""

    def __init__(
        self,
        message: str,
        *,
        release_id: int | None = None,
        tracker: str | None = None,
        permanent: bool = True,
    ):
        super().__init__(message)
        self.release_id = release_id
        self.tracker = tracker
        self.permanent = permanent


class NoSource(BackendError):
    """Agotados todos los candidatos: no hay fuente reproducible."""

    def __init__(self, message: str = "sin fuente disponible",
                 *, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


class NoPlayIntent(BackendError):
    pass


class NoSeedCommitment(BackendError):
    pass


class NoPolicy(BackendError):
    pass


class CapacityError(BackendError):
    pass


class QbitWrite(Protocol):
    def torrents(self, category=None) -> list[dict]: ...
    def files(self, info_hash: str) -> list[dict]: ...
    def add_torrent(self, torrent_bytes: bytes, **kwargs: Any) -> str: ...
    def set_file_priorities(self, info_hash: str, priorities: dict[int, int]) -> None: ...
    def set_force_start(self, info_hash: str, value: bool = True) -> None: ...
    def pause(self, info_hash: str) -> None: ...
    def resume(self, info_hash: str) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


LOGIN_MARKERS = (
    b"<html", b"<title>login", b"login -", b"iniciar sesion",
    b"iniciar sesi", b"(removed)", b"torrent not found",
    b"not found", b"access denied", b"acceso denegado",
)


def _looks_like_login(raw: bytes) -> bool:
    probe = (raw or b"")[:4096].lower()
    return any(marker in probe for marker in LOGIN_MARKERS)


def validate_torrent(raw: bytes) -> bytes:
    """Comprueba que la respuesta es un bencode con diccionario `info`."""
    if not raw or not raw.startswith(b"d"):
        permanent = _looks_like_login(raw) or bool(raw)
        raise TorrentFetchError(
            "la descarga no es un .torrent válido",
            permanent=permanent,
            body=raw,
        )
    if info_dict_bytes(raw) is None:
        raise TorrentFetchError(
            "el .torrent no contiene el diccionario info",
            permanent=True,
            body=raw,
        )
    return raw


def fetch_torrent(url: str, *, jackett_api_key: str | None = None,
                  timeout: float = 30.0) -> bytes:
    if not url:
        raise TorrentFetchError("release sin download_url", permanent=True)
    if "jackett_apikey" not in url and jackett_api_key and "/dl/" in url:
        url = with_api_key(url, jackett_api_key)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "catalogo-backend/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()[:512]
        except Exception:
            pass
        permanent = (
            exc.code in (401, 403, 404, 410)
            or _looks_like_login(body)
        )
        raise TorrentFetchError(
            f"el índice devolvió HTTP {exc.code}",
            status=exc.code,
            permanent=permanent,
            body=body,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TorrentFetchError(
            f"no se pudo descargar el .torrent: {str(exc)[:150]}",
            permanent=False,
        ) from exc
    return validate_torrent(raw)


def _mark_release_dead(
    db_path: str,
    release_id: int,
    *,
    reason: str,
    now: datetime,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE releases
            SET health = 'dead', health_checked_at = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (now.isoformat(), int(release_id)),
        )
        conn.execute(
            """
            UPDATE seed_commitments
            SET state = 'cancelled', reason = ?, completed_at = ?
            WHERE release_id = ? AND state = 'approved'
              AND NOT EXISTS (
                  SELECT 1 FROM torrents t WHERE t.release_id = ?
              )
            """,
            (reason, now.isoformat(), int(release_id), int(release_id)),
        )
        conn.commit()


def info_hash(torrent_bytes: bytes) -> str:
    info = info_dict_bytes(torrent_bytes)
    if not info:
        raise BackendError("torrent sin diccionario info")
    return hashlib.sha1(info).hexdigest()


def _is_video(name: str) -> bool:
    return Path(name or "").suffix.lower() in VIDEO_EXT


def select_target(files: list[dict], media: Any) -> dict:
    videos = [item for item in files if _is_video(item.get("name") or "")]
    if not videos:
        raise BackendError("el torrent no tiene vídeo")
    if media["kind"] == "series" and media["season"] is not None:
        season = int(media["season"])
        episode = int(media["episode"] or 0)
        for item in videos:
            if _episode_from_name(item.get("name") or "") == (season, episode):
                return item
    return max(videos, key=lambda item: int(item.get("size") or 0))


def priorities_for(
    files: list[dict],
    target_index: int,
    *,
    private: bool,
) -> dict[int, int]:
    """Privados: todo se descarga; el capítulo pedido va por delante.

    Prioridad 6 (alta), no 7 (máxima): con 7 qBittorrent reparte las piezas
    fuera de orden y el buffer contiguo no crece, así que el stream no puede
    arrancar hasta terminar la descarga. Con 6 se mantiene el orden
    secuencial y el fichero pedido se descarga primero.

    Públicos: solo se pide el fichero objetivo (parcial permitido).
    """
    result: dict[int, int] = {}
    for item in files:
        index = int(item.get("index") or 0)
        if private:
            result[index] = 6 if index == target_index else 1
        else:
            result[index] = 1 if index == target_index else 0
    return result


def _active_ondemand(
    client: QbitWrite,
    category: str,
    exclude: str | None = None,
    *,
    torrents: list[dict] | None = None,
) -> int:
    # Solo descargas reales: queued/checking/metadata no ocupan un slot.
    active = {"downloading", "forceddl", "stalleddl"}
    items = torrents if torrents is not None else client.torrents(
        category=category
    )
    count = 0
    for torrent in items:
        if torrents is not None and (torrent.get("category") or "") != category:
            continue
        state = (torrent.get("state") or "").lower()
        if (torrent.get("hash") or "").lower() == (exclude or "").lower():
            continue
        if state in active:
            count += 1
    return count


def _wait_files(client: QbitWrite, hash_: str, *, timeout: float = 6.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            files = client.files(hash_)
        except QbittorrentError:
            files = []
        if files:
            return files
        time.sleep(0.5)
    raise BackendError("qBittorrent no devolvió los ficheros del torrent")


def prepare_download(
    db_path: str,
    media_id: str,
    client: QbitWrite,
    *,
    torrent_bytes: bytes | None = None,
    jackett_api_key: str | None = None,
    save_path: str = DEFAULT_SAVE_PATH,
    category: str = DEFAULT_CATEGORY,
    max_active: int = MAX_ACTIVE_ONDEMAND,
    release_id: int | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    from . import play_intents

    current = now or _now()
    with closing(connect(db_path)) as conn:
        media = conn.execute(
            "SELECT * FROM media_items WHERE media_id = ? OR key = ? LIMIT 1",
            (media_id, media_id),
        ).fetchone()
        if media is None:
            raise BackendError(f"media no encontrado: {media_id}")

        intent = play_intents.active(db_path, media["key"], now=current)
        if intent is None:
            raise NoPlayIntent("no hay play_intent activo")

        commitment = conn.execute(
            """
            SELECT * FROM seed_commitments
            WHERE asset_id = ? AND state IN ('approved', 'active', 'seeding')
              AND (? IS NULL OR release_id = ?)
            ORDER BY id DESC LIMIT 1
            """,
            (media["id"], release_id, release_id),
        ).fetchone()
        if commitment is None:
            raise NoSeedCommitment("no hay seed_commitment aprobado")

        release = conn.execute(
            "SELECT * FROM releases WHERE id = ?", (commitment["release_id"],)
        ).fetchone()
        if release is None:
            raise BackendError("el commit apunta a un release inexistente")

        policy = get_policy(conn, release["tracker"])
        if policy is None or not policy.enabled:
            raise NoPolicy(f"sin política para {release['tracker']}")
        failed = conn.execute(
            """
            SELECT 1 FROM stream_failures
            WHERE release_id = ?
              AND until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (int(release["id"]),),
        ).fetchone()
        if failed is not None:
            raise ReleaseUnavailable(
                "swarm descartado: "
                f"{release['tracker']} release {release['id']}",
                release_id=int(release["id"]),
                tracker=release["tracker"],
                permanent=False,
            )

    # Consulta directa por hash: listar cientos de torrents hace que la app
    # agote su timeout antes de recibir el 202.
    def _find_live(hash_value: str | None) -> Any | None:
        wanted = (hash_value or "").lower()
        if not wanted:
            return None
        direct = getattr(client, "torrent", None)
        if callable(direct):
            try:
                found = direct(wanted)
            except Exception:
                found = None
            if found is not None:
                return found
        for torrent in client.torrents():
            if (torrent.get("hash") or "").lower() == wanted:
                return torrent
        return None

    def _usable(torrent: Any | None) -> Any | None:
        """Descarta un torrent roto por apagón o ficheros perdidos."""
        if torrent is None:
            return None
        state = (torrent.get("state") or "").lower()
        if "error" not in state and "missing" not in state:
            return torrent
        try:
            client.delete(torrent.get("hash") or "", delete_files=False)
        except Exception:
            pass
        return None

    # Si el torrent ya está en qBittorrent (descargando o sembrando), no se
    # necesita volver a bajar el .torrent ni se debe cancelar su compromiso.
    existing = _usable(_find_live(release["info_hash"]))
    hash_ = (existing.get("hash") or "").lower() if existing else ""
    existing_state = (existing.get("state") or "").lower() if existing else ""

    if existing is None:
        if torrent_bytes is None and release["torrent_blob"]:
            blob = bytes(release["torrent_blob"])
            try:
                torrent_bytes = validate_torrent(blob)
            except TorrentFetchError:
                torrent_bytes = None
        if torrent_bytes is None:
            try:
                torrent_bytes = fetch_torrent(
                    release["download_url"], jackett_api_key=jackett_api_key
                )
            except TorrentFetchError as exc:
                if exc.permanent:
                    _mark_release_dead(
                        db_path,
                        int(release["id"]),
                        reason="release_unavailable",
                        now=current,
                    )
                detail = f"{release['tracker']} release {release['id']}: {exc}"
                raise ReleaseUnavailable(
                    detail,
                    release_id=int(release["id"]),
                    tracker=release["tracker"],
                    permanent=exc.permanent,
                ) from exc
        torrent_bytes = validate_torrent(torrent_bytes)
        hash_ = info_hash(torrent_bytes)
        existing = _usable(_find_live(hash_))
        existing_state = (
            (existing.get("state") or "").lower() if existing else ""
        )
    was_paused = existing_state.startswith(("paused", "stopped"))
    if existing is None:
        active = _active_ondemand(client, category)
        if not force and active >= max_active:
            raise CapacityError(f"ya hay {max_active} ondemand activos")
        client.add_torrent(
            torrent_bytes,
            save_path=save_path,
            category=category,
            paused=True,
            sequential=True,
            first_last=True,
            use_download_path=False,
            filename=f"{hash_}.torrent",
        )
    # No se pausa un torrent que ya está corriendo. Si es nuevo, ya se añadió
    # paused; si ya existía, se respeta su estado. Pausar/reanudar en cada
    # Range provoca announces duplicados y el tracker bloquea por intervalo.
    files = _wait_files(client, hash_)
    target = select_target(files, media)
    target_index = int(target.get("index") or 0)
    priorities = priorities_for(
        files, target_index, private=(policy.privacy != "public")
    )
    client.set_file_priorities(hash_, priorities)

    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE seed_commitments
            SET state = 'active',
                started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (current.isoformat(), commitment["id"]),
        )
        conn.execute(
            """
            INSERT INTO torrents(info_hash, release_id, tracker, state, save_path,
                                 added_at, last_sync, category, qbit_state,
                                 progress, base_path)
            VALUES (?, ?, ?, 'waiting', ?, ?, ?, ?, 'pausedDL', 0, ?)
            ON CONFLICT(info_hash) DO UPDATE SET
                release_id = COALESCE(torrents.release_id, excluded.release_id),
                category = excluded.category,
                save_path = excluded.save_path,
                base_path = excluded.base_path,
                qbit_state = excluded.qbit_state,
                last_sync = excluded.last_sync,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                hash_,
                release["id"],
                release["tracker"],
                save_path,
                current.isoformat(),
                current.isoformat(),
                category,
                save_path,
            ),
        )
        torrent_row = conn.execute(
            "SELECT id FROM torrents WHERE info_hash = ?", (hash_,)
        ).fetchone()
        target_file_id = None
        for item in files:
            index = int(item.get("index") or 0)
            rel_path = item.get("name") or ""
            size = int(item.get("size") or 0)
            local_path = str(Path(save_path) / rel_path)
            asset_id = int(media["id"]) if index == target_index else None
            existing_file = conn.execute(
                "SELECT id FROM torrent_files WHERE torrent_id = ? AND file_index = ?",
                (int(torrent_row["id"]), index),
            ).fetchone()
            if existing_file:
                conn.execute(
                    """
                    UPDATE torrent_files SET
                        asset_id = COALESCE(?, asset_id), rel_path = ?,
                        local_path = ?, size_bytes = ?, state = 'downloading',
                        storage_location = ?, priority = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (asset_id, rel_path, local_path, size,
                     _storage_for(save_path), int(priorities.get(index, 0)),
                     int(existing_file["id"])),
                )
                file_id = int(existing_file["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO torrent_files(asset_id, release_id, torrent_id,
                                              file_index, rel_path, local_path,
                                              size_bytes, state, storage_location,
                                              priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'downloading', ?, ?)
                    """,
                    (asset_id, release["id"], int(torrent_row["id"]), index,
                     rel_path, local_path, size, _storage_for(save_path),
                     int(priorities.get(index, 0))),
                )
                file_id = int(cur.lastrowid)
            if index == target_index:
                target_file_id = file_id
        # El fichero del episodio apunta a SU release aunque el torrent sea
        # un pack compartido. Así /status devuelve el release_id correcto.
        conn.execute(
            "UPDATE torrent_files SET release_id = ? "
            "WHERE torrent_id = ? AND file_index = ?",
            (int(release["id"]), int(torrent_row["id"]), target_index),
        )
        conn.execute(
            "UPDATE media_items SET status = 'DOWNLOADING_LOCAL', "
            "active_file_id = COALESCE(?, active_file_id), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (target_file_id, int(media["id"])),
        )
        conn.commit()

    play_intents.consume(db_path, intent["id"], now=current)
    # Solo se arranca una vez: torrent nuevo o que estaba pausado. Si ya está
    # descargando, no se toca para no repetir announce en el tracker.
    # force=True es una orden manual: se fuerza aunque ya exista y esté en
    # cola, para saltarse la cola global ocupada por Radarr/Sonarr.
    started = existing is None or was_paused
    if force or started:
        client.resume(hash_)
        client.set_force_start(hash_, True)
    return {
        "media_id": media["media_id"],
        "info_hash": hash_,
        "release_id": int(release["id"]),
        "file_index": target_index,
        "target_name": target.get("name"),
        "priorities": priorities,
        "private": policy.privacy != "public",
        "resumed": started or force,
        "forced": bool(force),
        "already_running": bool(existing is not None and not was_paused),
    }
