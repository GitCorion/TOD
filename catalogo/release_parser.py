"""Parser determinista de metadatos de release al estilo Sonarr/Radarr.

Extrae resolución, fuente (WEB-DL, BluRay, Remux, MicroHD, BRDISK...), códec
de vídeo, HDR/DoVi, idioma y códec de audio, canales, servicio de streaming,
edición, PROPER/REPACK y grupo de release desde títulos scene y trackers
españoles.
"""

from __future__ import annotations

import json
import re
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from .db import connect

QUALITY_RE = re.compile(
    r"(?i)(?<![a-z0-9])(2160p|4k|uhd|1080p|720p|480p|576p|360p)(?![a-z0-9])"
)

# Disc images / unstreamable containers must be flagged clearly.
_BRDISK_RE = re.compile(
    r"(?i)\b(?:br-?disk|bdmv|bd-?iso|bd25|bd50|uhd[-.\s]?bdmv|full[-.\s]?blu-?ray|dvd-?r|dvd5|dvd9|iso)\b"
)
_CAM_RE = re.compile(
    r"(?i)\b(?:hd-?cam|cam-?rip|cam|hd-?ts|ts-?rip|telesync|telecine|screener|dvd-?scr|scr|workprint)\b"
)
_REMUX_RE = re.compile(r"(?i)\b(?:uhd[-.\s]?)?(?:bd)?remux\b")
_MICRO_RE = re.compile(r"(?i)\b(?:micro-?hd|micro-?4k|m1080p|m720p|m2160p)\b")
_WEBDL_RE = re.compile(
    r"(?i)\b(?:web[-.\s_]?dl|webdl|wed[-.\s_]?dl)\b"
    r"|\b(?:nf|amzn|dsnp|atvp|hmax|hbo|pmtp|crunchyroll|pcok|stan|filmin|movistar)[-.\s_]+web\b"
)
_WEBRIP_RE = re.compile(r"(?i)\b(?:web[-.\s_]?rip|web[-.\s_]?cap|webrip)\b")
_WEB_GENERIC_RE = re.compile(r"(?i)(?<![a-z0-9])web(?![a-z0-9])")
_BLURAY_RE = re.compile(r"(?i)\b(?:uhd[-.\s_]?)?(?:blu-?ray|bd-?rip|br-?rip)\b")
_BDRIP_ONLY_RE = re.compile(r"(?i)\b(?:bd-?rip|br-?rip)\b")
_HDTV_RE = re.compile(r"(?i)\b(?:hd-?tv(?:rip)?|pdtv|satrip|dvb)\b")
_DVD_RE = re.compile(r"(?i)\b(?:dvd-?rip|dvdrip)\b")
_HDRIP_RE = re.compile(r"(?i)\bhd-?rip\b")

# Video codecs (allow trailing/leading stickiness like HDR10HEVC seen in real releases)
_HEVC_RE = re.compile(r"(?i)(?:[xh]\.?265|hevc)(?![a-z0-9])")
_H264_RE = re.compile(r"(?i)(?:[xh]\.?264|\bavc\b)(?![a-z0-9])")
_AV1_RE = re.compile(r"(?i)(?<![a-z0-9])av1(?![a-z0-9])")
_XVID_RE = re.compile(r"(?i)\b(?:xvid|divx)\b")
_TENBIT_RE = re.compile(r"(?i)\b(?:10-?bit|hi10p?)\b")

# HDR / Dolby Vision
_DV_RE = re.compile(r"(?i)\b(?:dovi|dolby[-.\s]?vision|dv)\b")
_HDR10P_RE = re.compile(r"(?i)\bhdr10\+")
_HDR10_RE = re.compile(r"(?i)\bhdr10\b|\bhdr10(?=hevc|x265|x264)")
_HDR_RE = re.compile(r"(?i)\bhdr\b")
_HLG_RE = re.compile(r"(?i)\bhlg\b")

# Audio languages
_VOSE_TAG_RE = re.compile(
    r"(?i)\b(?:vose|vos|subs?[ ._-]*(?:esp|spa|spanish|castellano))\b"
)
_CAST_STRICT_RE = re.compile(r"(?i)\b(?:castellano|spa[-_]es|es[-_]es|cast)\b")
_CAST_ES_AUDIO_RE = re.compile(
    r"(?i)(?<![a-z0-9])es(?:\s+(?:dd\+?|ddp|eac3|ac3|aac|dts|true-?hd|atmos|pcm|lpcm|subs?|\d\.\d))"
)
_CAST_GENERIC_RE = re.compile(
    r"(?i)\b(?:castellano|espa[ñn]ol|espanol|spanish|spa|esp|es)\b"
)
_LATINO_RE = re.compile(r"(?i)\b(?:latino|latam|lat|es-?la|es-?419)\b")
_LATINO_TAG_RE = re.compile(
    r"(?i)\b(?:latino|latam|es-?la|es-?419)\b"
    r"|\blat(?=\s+(?:dd\+?|ddp|eac3|ac3|aac|dts|true-?hd|atmos|subs?|\d\.\d|\]))"
)
_MULTI_RE = re.compile(
    r"(?i)\b(?:multi(?:audio)?|dual(?:[-.\s]?audio)?|tri-?audio)\b"
)
_ENGLISH_ONLY_RE = re.compile(r"(?i)\b(?:english|eng|v\.?o\.?)\b")

# Audio codecs & channels
_TRUEHD_RE = re.compile(r"(?i)\btrue-?hd\b")
_ATMOS_RE = re.compile(r"(?i)\batmos\b")
_DTS_HD_MA_RE = re.compile(r"(?i)\bdts[-.\s]?hd(?:[-.\s]?ma)?\b")
_DTS_X_RE = re.compile(r"(?i)\bdts[-.: ]?x\b")
_DTS_RE = re.compile(r"(?i)\bdts\b")
_EAC3_RE = re.compile(r"(?i)\b(?:dd\+|ddp|e-?ac-?3|eac3)(?![a-z])")
_AC3_RE = re.compile(
    r"(?i)\b(?:ac-?3|dd(?:\s*[25]\.?[01])|dolby[-.\s]?digital)\b"
)
_AAC_RE = re.compile(r"(?i)\baac\b")
_FLAC_RE = re.compile(r"(?i)\bflac\b")
_OPUS_RE = re.compile(r"(?i)\bopus\b")
_PCM_RE = re.compile(r"(?i)\b(?:l?pcm)\b")
_CHANNELS_RE = re.compile(r"(?i)(?<![0-9])([1257])[.\s]([01])(?![0-9])")

# Streaming services
_STREAMING_RE = re.compile(
    r"(?i)\b(nf|netflix|amzn|amazon|dsnp|disney\+?|atvp|aptv|appletv\+?|hmax|hbomax|hbo|pmtp|crunchyroll|cr|pcok|peacock|stan|filmin|movistar|atresplayer)\b"
)
_STREAMING_MAP = {
    "netflix": "NF", "nf": "NF",
    "amazon": "AMZN", "amzn": "AMZN",
    "disney": "DSNP", "disney+": "DSNP", "dsnp": "DSNP",
    "appletv": "ATVP", "appletv+": "ATVP", "atvp": "ATVP", "aptv": "ATVP",
    "hbomax": "HMAX", "hmax": "HMAX", "hbo": "HBO",
    "pmtp": "PMTP",
    "crunchyroll": "CRUNCHYROLL", "cr": "CRUNCHYROLL",
    "peacock": "PCOK", "pcok": "PCOK",
    "stan": "STAN",
    "filmin": "FILMIN",
    "movistar": "MOVISTAR",
    "atresplayer": "ATRESPLAYER",
}

# Edition / proper / pack / release group
_EDITION_PATTERNS = (
    (re.compile(r"(?i)\bdirector'?s?[-.\s]?cut\b"), "Director's Cut"),
    (re.compile(r"(?i)\bextended(?:[-.\s]?(?:cut|edition))?\b"), "Extended"),
    (re.compile(r"(?i)\bimax\b"), "IMAX"),
    (re.compile(r"(?i)\bunrated\b"), "Unrated"),
    (re.compile(r"(?i)\bremastered\b"), "Remastered"),
    (re.compile(r"(?i)\buncut\b"), "Uncut"),
    (re.compile(r"(?i)\bfinal[-.\s]?cut\b"), "Final Cut"),
    (re.compile(r"(?i)\bopen[-.\s]?matte\b"), "Open Matte"),
    (
        re.compile(r"(?i)\be(?:dici[oó]n)?[-.\s]?especial(?:\s+\d+\s+discos?)?\b"),
        "Edición Especial",
    ),
    (re.compile(r"(?i)\baudio[-.\s]?editado\b"), "Audio Editado"),
)
_PROPER_RE = re.compile(r"(?i)\b(?:proper|repack)\b")
_REPACK2_RE = re.compile(r"(?i)\b(?:repack2|repack3|proper2|real[-.\s]?proper)\b")
_HARDCODED_RE = re.compile(
    r"(?i)\b(?:hc[-.\s]?subs?|hardcoded|korsub|sub[-.\s]?forzado)\b"
)

_TRAILER_EXT_RE = re.compile(r"(?i)\.(?:mkv|mp4|avi|ts|m2ts|iso)$")
_END_LANG_STRIP_RE = re.compile(r"(?i)\s+(?:spanish|english|multi|dual|latino)$")
_BRACKET_GROUP_RE = re.compile(r"\[([A-Za-z0-9._-]{2,20})\](?:\.[a-z0-9]{2,4})?$")
_DOT_TAG_GROUP_RE = re.compile(r"\.([A-Z0-9]{2,12})(?:\.(?:mkv|mp4|avi))?$")
_NON_GROUP_TOKENS = {
    "mkv", "mp4", "avi", "spanish", "english", "castellano", "latino",
    "vose", "multi", "dual", "pack", "subs", "hdr", "hdr10", "dv", "dovi",
    "hevc", "avc", "x264", "x265", "h264", "h265", "1080p", "2160p", "720p",
    "480p", "4k", "uhd", "bluray", "web", "webdl", "remux", "ac3", "eac3",
    "aac", "dts", "truehd", "atmos", "10bit", "8bit",
}


@dataclass(frozen=True)
class ReleaseInfo:
    quality: str
    source_type: str | None
    codec: str | None
    hdr: str | None
    audio: str
    audio_codec: str | None
    audio_channels: str | None
    edition: str | None
    release_group: str | None
    proper_rank: int
    streaming_service: str | None
    flags: dict[str, Any]

    @property
    def quality_tier(self) -> str:
        res = self.quality if self.quality != "unknown" else ""
        src_labels = {
            "remux": "Remux",
            "bluray": "BluRay",
            "webdl": "WEB-DL",
            "webrip": "WEBRip",
            "microhd": "MicroHD",
            "bdrip": "BDRip",
            "hdtv": "HDTV",
            "dvd": "DVDRip",
            "brdisk": "BRDISK",
            "cam": "CAM",
            "hdrip": "HDRip",
        }
        src = src_labels.get(self.source_type or "", "")
        if src and res:
            return f"{src} {res}"
        return src or res or "Desconocida"

    EXTRA_COLS = (
        "source_type",
        "codec",
        "hdr",
        "audio_codec",
        "audio_channels",
        "edition",
        "release_group",
        "proper_rank",
        "streaming_service",
        "parsed_flags",
    )

    def to_db_fields(self) -> dict[str, Any]:
        return {
            "quality": self.quality,
            "source_type": self.source_type,
            "codec": self.codec,
            "hdr": self.hdr,
            "audio": self.audio,
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "edition": self.edition,
            "release_group": self.release_group,
            "proper_rank": self.proper_rank,
            "streaming_service": self.streaming_service,
            "parsed_flags": json.dumps(
                self.flags, ensure_ascii=False, sort_keys=True
            )
            if self.flags
            else None,
        }

    def extra_db_tuple(self) -> tuple[Any, ...]:
        fields = self.to_db_fields()
        return tuple(fields[col] for col in self.EXTRA_COLS)


def quality_of(title: str | None) -> str:
    match = QUALITY_RE.search(title or "")
    if not match:
        return "unknown"
    value = match.group(1).lower()
    return "4K" if value in ("2160p", "4k", "uhd") else value


def audio_of(title: str | None) -> str:
    text = title or ""
    has_vose = bool(_VOSE_TAG_RE.search(text))
    cleaned_for_cast = _VOSE_TAG_RE.sub(" ", text)
    has_cast_strict = bool(
        _CAST_STRICT_RE.search(cleaned_for_cast)
        or _CAST_ES_AUDIO_RE.search(cleaned_for_cast)
    )
    has_cast_generic = bool(_CAST_GENERIC_RE.search(cleaned_for_cast))
    has_latino = bool(_LATINO_RE.search(text))

    if has_vose and not has_cast_generic:
        return "vose"
    if has_latino:
        if has_cast_strict:
            return "castellano"
        if _LATINO_TAG_RE.search(text) or not has_cast_generic:
            return "latino"
    if has_cast_generic:
        return "castellano"
    return "unknown"


def _source_type(text: str) -> str | None:
    if _CAM_RE.search(text):
        return "cam"
    if _BRDISK_RE.search(text):
        return "brdisk"
    if _REMUX_RE.search(text):
        return "remux"
    if _MICRO_RE.search(text):
        return "microhd"
    if _WEBDL_RE.search(text):
        return "webdl"
    if _WEBRIP_RE.search(text):
        return "webrip"
    if _BDRIP_ONLY_RE.search(text):
        return "bdrip"
    if _BLURAY_RE.search(text):
        return "bluray"
    if _WEB_GENERIC_RE.search(text):
        return "webrip"
    if _HDTV_RE.search(text):
        return "hdtv"
    if _HDRIP_RE.search(text):
        return "hdrip"
    if _DVD_RE.search(text):
        return "dvd"
    return None


def _video_codec(text: str) -> str | None:
    if _HEVC_RE.search(text):
        return "HEVC"
    if _H264_RE.search(text):
        return "H264"
    if _AV1_RE.search(text):
        return "AV1"
    if _XVID_RE.search(text):
        return "XviD"
    return None


def _hdr_format(text: str) -> str | None:
    dv = bool(_DV_RE.search(text))
    hdr10p = bool(_HDR10P_RE.search(text))
    hdr10 = bool(_HDR10_RE.search(text))
    hdr = bool(_HDR_RE.search(text))
    hlg = bool(_HLG_RE.search(text))
    if dv and hdr10p:
        return "DV-HDR10+"
    if dv and hdr10:
        return "DV-HDR10"
    if dv and hdr:
        return "DV-HDR"
    if dv:
        return "DV"
    if hdr10p:
        return "HDR10+"
    if hdr10:
        return "HDR10"
    if hdr:
        return "HDR"
    if hlg:
        return "HLG"
    return None


def _audio_spec(text: str) -> tuple[str | None, str | None]:
    codec = None
    has_atmos = bool(_ATMOS_RE.search(text))
    if _TRUEHD_RE.search(text):
        codec = "TrueHD Atmos" if has_atmos else "TrueHD"
    elif _DTS_X_RE.search(text):
        codec = "DTS:X"
    elif _DTS_HD_MA_RE.search(text):
        codec = "DTS-HD MA"
    elif _EAC3_RE.search(text):
        codec = "DD+ Atmos" if has_atmos else "EAC3"
    elif has_atmos:
        codec = "Atmos"
    elif _DTS_RE.search(text):
        codec = "DTS"
    elif _AC3_RE.search(text):
        codec = "AC3"
    elif _FLAC_RE.search(text):
        codec = "FLAC"
    elif _PCM_RE.search(text):
        codec = "PCM"
    elif _OPUS_RE.search(text):
        codec = "Opus"
    elif _AAC_RE.search(text):
        codec = "AAC"

    channels = None
    matches = _CHANNELS_RE.findall(text)
    for major, minor in reversed(matches):
        cand = f"{major}.{minor}"
        if cand in ("7.1", "5.1", "2.0", "1.0"):
            channels = cand
            break
    return codec, channels


def _streaming_service(text: str) -> str | None:
    m = _STREAMING_RE.search(text)
    if not m:
        return None
    return _STREAMING_MAP.get(m.group(1).lower())


def _edition(text: str) -> str | None:
    editions = [
        label for pattern, label in _EDITION_PATTERNS if pattern.search(text)
    ]
    return " / ".join(dict.fromkeys(editions)) if editions else None


def _proper_rank(text: str) -> int:
    if _REPACK2_RE.search(text):
        return 2
    if _PROPER_RE.search(text):
        return 1
    return 0


def _release_group(text: str) -> str | None:
    cleaned = _END_LANG_STRIP_RE.sub("", text.strip())
    cleaned = _TRAILER_EXT_RE.sub("", cleaned).strip()
    m = _BRACKET_GROUP_RE.search(cleaned)
    if m and m.group(1).lower() not in _NON_GROUP_TOKENS:
        return m.group(1)
    m = re.search(r"-([A-Za-z0-9]{2,20})$", cleaned)
    if m and m.group(1).lower() not in _NON_GROUP_TOKENS:
        return m.group(1)
    m = _DOT_TAG_GROUP_RE.search(cleaned)
    if m and m.group(1).lower() not in _NON_GROUP_TOKENS:
        return m.group(1)
    return None


def parse_release_info(title: str | None) -> ReleaseInfo:
    text = str(title or "").strip()
    quality = quality_of(text)
    source_type = _source_type(text)
    codec = _video_codec(text)
    hdr = _hdr_format(text)
    audio = audio_of(text)
    audio_codec, audio_channels = _audio_spec(text)
    edition = _edition(text)
    release_group = _release_group(text)
    proper_rank = _proper_rank(text)
    streaming_service = _streaming_service(text)

    flags: dict[str, Any] = {}
    if source_type == "brdisk":
        flags["disc_image"] = True
    if source_type == "cam":
        flags["cam_screener"] = True
    if _TENBIT_RE.search(text):
        flags["ten_bit"] = True
    if _MULTI_RE.search(text) or (
        bool(_LATINO_RE.search(text)) and bool(_CAST_STRICT_RE.search(text))
    ):
        flags["multi_audio"] = True
    if _HARDCODED_RE.search(text):
        flags["hardcoded_subs"] = True
    if _LATINO_RE.search(text):
        flags["has_latino_tag"] = True
    if _ENGLISH_ONLY_RE.search(text):
        flags["has_english_tag"] = True

    return ReleaseInfo(
        quality=quality,
        source_type=source_type,
        codec=codec,
        hdr=hdr,
        audio=audio,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        edition=edition,
        release_group=release_group,
        proper_rank=proper_rank,
        streaming_service=streaming_service,
        flags=flags,
    )


def backfill_releases(
    db_path: str, *, reparse_audio: bool = False
) -> dict[str, int]:
    """Rellena los metadatos enriquecidos de todos los releases ya indexados."""
    reclassified_latino = 0
    disc_images = 0
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, title, quality, audio, audio_confidence, codec FROM releases"
        ).fetchall()
        batch: list[tuple] = []
        for row in rows:
            info = parse_release_info(row["title"])
            new_quality = (
                info.quality
                if info.quality != "unknown"
                else (row["quality"] or "unknown")
            )
            new_audio = row["audio"] or "unknown"
            if reparse_audio or row["audio_confidence"] != "tracker":
                if info.audio != "unknown":
                    if new_audio == "castellano" and info.audio == "latino":
                        reclassified_latino += 1
                    new_audio = info.audio
            new_codec = info.codec or row["codec"]
            if info.flags.get("disc_image"):
                disc_images += 1
            fields = info.to_db_fields()
            batch.append((
                new_quality,
                fields["source_type"],
                new_codec,
                fields["hdr"],
                new_audio,
                fields["audio_codec"],
                fields["audio_channels"],
                fields["edition"],
                fields["release_group"],
                fields["proper_rank"],
                fields["streaming_service"],
                fields["parsed_flags"],
                int(row["id"]),
            ))
        conn.executemany(
            """
            UPDATE releases SET
                quality = ?,
                source_type = ?,
                codec = ?,
                hdr = ?,
                audio = ?,
                audio_codec = ?,
                audio_channels = ?,
                edition = ?,
                release_group = ?,
                proper_rank = ?,
                streaming_service = ?,
                parsed_flags = ?
            WHERE id = ?
            """,
            batch,
        )
        conn.execute(
            """
            UPDATE media_items SET is_anime = 1,
                series_type = CASE WHEN kind = 'series' THEN 'anime' ELSE series_type END
            WHERE lower(COALESCE(original_language, '')) = 'ja'
              AND (
                  lower(COALESCE(genres, '')) LIKE '%anim%'
                  OR lower(COALESCE(original_country, '')) LIKE '%jp%'
              )
            """
        )
        conn.commit()
    return {
        "releases_updated": len(batch),
        "reclassified_latino": reclassified_latino,
        "disc_images": disc_images,
    }
