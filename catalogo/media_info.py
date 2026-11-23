"""Metadatos de reproducción a partir del fichero parcial.

Jellyfin sondea el ``.strm`` en cada reproducción con ffprobe. Si el
torrent aún está descargando, el sondeo se atasca y el cliente espera al
fichero completo. TOD sí puede leer la cabecera del fichero parcial: los
MKV llevan streams y duración en el Segment Info, así que con los
primeros MB basta para devolver codecs, resolución, audio y duración.

El plugin de Jellyfin usa estos datos para guardar los ``MediaStream`` y
saltarse el probe remoto.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

from .db import connect
from .resolver import Resolver
from .stream import find_file

FFPROBE_CANDIDATES = (
    "/usr/lib/jellyfin-ffmpeg/ffprobe",
    "/usr/bin/ffprobe",
)
PROBE_TIMEOUT = 8.0
PROBE_SIZE = "8M"
ANALYZE_DURATION = "2M"
MIN_PROBE_BYTES = 2 * 1024 * 1024
CACHE_MAX = 512

_CACHE: dict[tuple[int, int], dict[str, Any]] = {}


def _ffprobe() -> str | None:
    for candidate in FFPROBE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("ffprobe")


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize(data: dict[str, Any], file_size: int | None) -> dict[str, Any]:
    fmt = data.get("format") or {}
    video = None
    audio: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    for item in data.get("streams") or []:
        kind = item.get("codec_type")
        tags = item.get("tags") or {}
        if kind == "video" and video is None:
            video = {
                "index": _as_int(item.get("index")) or 0,
                "codec": item.get("codec_name") or None,
                "width": _as_int(item.get("width")),
                "height": _as_int(item.get("height")),
                "bit_depth": (
                    _as_int(item.get("bits_per_raw_sample"))
                    or _as_int(item.get("bits_per_sample"))
                ),
                "profile": item.get("profile") or None,
                "pix_fmt": item.get("pix_fmt") or None,
                "color_transfer": item.get("color_transfer") or None,
                "color_primaries": item.get("color_primaries") or None,
            }
        elif kind == "audio":
            audio.append(
                {
                    "index": _as_int(item.get("index")) or 0,
                    "codec": item.get("codec_name") or None,
                    "channels": _as_int(item.get("channels")),
                    "language": tags.get("language") or None,
                    "title": tags.get("title") or None,
                }
            )
        elif kind == "subtitle":
            subtitles.append(
                {
                    "index": _as_int(item.get("index")) or 0,
                    "codec": item.get("codec_name") or None,
                    "language": tags.get("language") or None,
                    "title": tags.get("title") or None,
                }
            )
    return {
        "source": "ffprobe",
        "container": fmt.get("format_name") or None,
        "duration_seconds": _as_float(fmt.get("duration")),
        "size_bytes": file_size or _as_int(fmt.get("size")),
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
    }


def probe_file(path: Path, file_size: int | None = None) -> dict[str, Any] | None:
    """Lee la cabecera del fichero (aunque esté a medias)."""
    exe = _ffprobe()
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                "-probesize",
                PROBE_SIZE,
                "-analyzeduration",
                ANALYZE_DURATION,
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    payload = (result.stdout or "").strip()
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return _normalize(data, file_size)


def _title_codec(title: str, codec: str | None) -> str | None:
    text = f"{codec or ''} {title}".casefold()
    for needle, value in (
        ("hevc", "hevc"),
        ("h.265", "hevc"),
        ("h265", "hevc"),
        ("x265", "hevc"),
        ("avc", "h264"),
        ("h.264", "h264"),
        ("h264", "h264"),
        ("x264", "h264"),
        ("av1", "av1"),
        ("xvid", "mpeg4"),
        ("divx", "mpeg4"),
    ):
        if needle in text:
            return value
    return None


def _title_audio(title: str, audio: str | None) -> tuple[str | None, int]:
    text = f"{audio or ''} {title}".casefold()
    codec = None
    for needle, value in (
        ("dd+", "eac3"),
        ("ddp", "eac3"),
        ("eac3", "eac3"),
        ("atmos", "eac3"),
        ("ac3", "ac3"),
        ("aac", "aac"),
        ("dts", "dts"),
        ("truehd", "truehd"),
        ("flac", "flac"),
        ("mp3", "mp3"),
    ):
        if needle in text:
            codec = value
            break
    channels = 2
    if "7.1" in text:
        channels = 8
    elif "5.1" in text:
        channels = 6
    elif "2.0" in text:
        channels = 2
    return codec, channels


def _title_language(title: str) -> str:
    text = title.casefold()
    if any(item in text for item in ("vose", "english", "eng", "vo ")):
        return "eng"
    return "spa"


def _release_fallback(
    asset: Any,
    release: Any,
    *,
    key: str,
    release_id: int | None,
    file_size: int | None,
) -> dict[str, Any]:
    title = str(
        (release["title"] if release is not None else "")
        or asset["title"]
        or ""
    )
    quality = str(
        (release["quality"] if release is not None else "") or ""
    ).casefold()
    text = f"{title} {quality}".casefold()
    if "2160p" in text or quality in ("4k", "uhd"):
        width, height = 3840, 2160
    elif "720p" in text:
        width, height = 1280, 720
    else:
        width, height = 1920, 1080
    codec = _title_codec(
        title, release["codec"] if release is not None else None
    )
    audio_codec, channels = _title_audio(
        title, release["audio"] if release is not None else None
    )
    duration = None
    try:
        duration = float(asset["runtime_seconds"] or 0) or None
    except (TypeError, ValueError):
        duration = None
    size = file_size
    if size is None and release is not None:
        size = _as_int(release["size_bytes"])
    return {
        "key": key,
        "asset_id": int(asset["id"]),
        "release_id": release_id,
        "source": "release",
        "container": "matroska,webm",
        "duration_seconds": duration,
        "size_bytes": size,
        "video": {
            "index": 0,
            "codec": codec or "h264",
            "width": width,
            "height": height,
            "bit_depth": 10 if "10bit" in text else 8,
            "profile": None,
            "pix_fmt": None,
            "color_transfer": (
                "smpte2084"
                if any(item in text for item in ("dv", "hdr"))
                else None
            ),
            "color_primaries": None,
        },
        "audio": [
            {
                "index": 1,
                "codec": audio_codec or "aac",
                "channels": channels,
                "language": _title_language(title),
                "title": None,
            }
        ],
        "subtitles": [],
    }


def _file_path(
    conn: Any,
    *,
    asset_id: int,
    release_id: int | None,
    file_index: int | None,
    save_path: str | None,
    roots: list[str] | None,
) -> Path | None:
    row = None
    if release_id is not None and file_index is not None:
        row = conn.execute(
            """
            SELECT local_path, library_path, rel_path FROM torrent_files
            WHERE release_id = ? AND file_index = ?
            ORDER BY id DESC LIMIT 1
            """,
            (int(release_id), int(file_index)),
        ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT local_path, library_path, rel_path FROM torrent_files
            WHERE asset_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (int(asset_id),),
        ).fetchone()
    if row is None:
        return None
    for value in (row["local_path"], row["library_path"]):
        if value and Path(value).is_file():
            return Path(value)
    rel_path = row["rel_path"] or ""
    if rel_path:
        if save_path:
            candidate = Path(save_path) / rel_path
            if candidate.is_file():
                return candidate
        found = find_file(rel_path, roots)
        if found is not None:
            return found
    return None


def build_media_info(
    db_path: str,
    key: str,
    *,
    release_id: int | None = None,
    roots: list[str] | None = None,
) -> dict[str, Any] | None:
    """Devuelve los metadatos de reproducción del título pedido."""
    resolver = Resolver(db_path)
    try:
        resolution = (
            resolver.resolve_release(key, int(release_id))
            if release_id is not None
            else resolver.resolve(key)
        )
    except Exception:
        resolution = None
    if resolution is not None:
        release_id = resolution.release_id or release_id
    with closing(connect(db_path)) as conn:
        asset = conn.execute(
            "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
        if asset is None:
            return None
        release = None
        if release_id is not None:
            release = conn.execute(
                "SELECT * FROM releases WHERE id = ? AND asset_id = ?",
                (int(release_id), int(asset["id"])),
            ).fetchone()
        file_size = None
        file_index = resolution.file_index if resolution is not None else None
        save_path = resolution.save_path if resolution is not None else None
        path: Path | None = None
        if resolution is not None and resolution.path:
            candidate = Path(resolution.path)
            if candidate.is_file():
                path = candidate
        if path is None:
            path = _file_path(
                conn,
                asset_id=int(asset["id"]),
                release_id=release_id,
                file_index=file_index,
                save_path=save_path,
                roots=roots,
            )
        if path is not None:
            try:
                file_size = path.stat().st_size
            except OSError:
                file_size = None
        cache_key = None
        if release_id is not None and file_index is not None:
            cache_key = (int(release_id), int(file_index))
        if cache_key is not None and cache_key in _CACHE:
            cached = dict(_CACHE[cache_key])
            cached["key"] = key
            return cached
        if path is not None and file_size and file_size >= MIN_PROBE_BYTES:
            info = probe_file(path, file_size)
            if info is not None and info.get("video") is not None:
                info["key"] = key
                info["asset_id"] = int(asset["id"])
                info["release_id"] = release_id
                if not info.get("duration_seconds"):
                    info["duration_seconds"] = (
                        _as_float(asset["runtime_seconds"])
                    )
                if cache_key is not None:
                    _CACHE[cache_key] = dict(info)
                    if len(_CACHE) > CACHE_MAX:
                        _CACHE.pop(next(iter(_CACHE)))
                return info
        return _release_fallback(
            asset,
            release,
            key=key,
            release_id=release_id,
            file_size=file_size,
        )
