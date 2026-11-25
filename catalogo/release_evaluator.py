"""Evaluador de releases según Perfil de Calidad, Custom Formats y Restricciones.

Determina si cada candidato es elegible, motivos concretos de rechazo,
puntuación Custom Format, bitrate estimado (MB/min) y cumplimiento de Cutoff
sin ejecutar descargas en qBittorrent (Cutoff Virtual On-Demand).
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import language_settings, profiles
from .db import connect
from .release_parser import ReleaseInfo, parse_release_info


@dataclass(frozen=True)
class ReleaseEvaluation:
    release_id: int
    allowed: bool
    rejections: tuple[str, ...]
    quality_tier: str
    quality_idx: int
    language_rank: int | None
    cf_score: int
    score_breakdown: tuple[tuple[str, int], ...]
    mb_per_min: float | None
    cutoff_met: bool
    sort_key: tuple

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "allowed": self.allowed,
            "rejections": list(self.rejections),
            "quality_tier": self.quality_tier,
            "cf_score": self.cf_score,
            "score_breakdown": [
                {"name": name, "score": score}
                for name, score in self.score_breakdown
            ],
            "mb_per_min": round(self.mb_per_min, 1) if self.mb_per_min is not None else None,
            "cutoff_met": self.cutoff_met,
        }


def _match_spec(spec: dict[str, Any], info: ReleaseInfo, release: dict[str, Any]) -> bool:
    kind = str(spec.get("type") or "").lower()
    val = str(spec.get("value") or "").strip()
    if not val:
        return False
    negate = bool(spec.get("negate"))
    matched = False
    options = [v.strip().lower() for v in val.split("|") if v.strip()]

    if kind == "source":
        matched = (info.source_type or "").lower() in options
    elif kind == "codec":
        matched = (info.codec or "").lower() in options
    elif kind == "hdr":
        hdr = (info.hdr or "").lower()
        matched = any(opt == hdr or opt in hdr.split("-") for opt in options) if hdr else False
    elif kind == "audio":
        matched = (info.audio or "").lower() in options
    elif kind == "audio_codec":
        ac = (info.audio_codec or "").lower()
        matched = any(opt in ac for opt in options) if ac else False
    elif kind == "tracker":
        trk = str(release.get("tracker") or "").lower()
        matched = any(opt in trk for opt in options)
    elif kind == "streaming":
        matched = (info.streaming_service or "").lower() in options
    elif kind == "edition":
        ed = (info.edition or "").lower()
        matched = any(opt in ed for opt in options) if ed else False
    elif kind == "proper":
        try:
            min_rank = int(re.sub(r"[^0-9]", "", val) or "1")
        except ValueError:
            min_rank = 1
        matched = info.proper_rank >= min_rank
    elif kind == "regex":
        try:
            matched = bool(re.search(val, str(release.get("title") or ""), re.IGNORECASE))
        except re.error:
            matched = False

    return (not matched) if negate else matched


def _custom_format_score(
    info: ReleaseInfo,
    release: dict[str, Any],
    custom_formats: list[dict[str, Any]],
    score_map: dict[int, int],
) -> tuple[int, list[tuple[str, int]]]:
    total = 0
    breakdown: list[tuple[str, int]] = []
    for cf in custom_formats:
        if not cf.get("enabled", True):
            continue
        score = int(score_map.get(int(cf["id"]), 0))
        if score == 0:
            continue
        specs = cf.get("specs") or []
        if not specs:
            continue
        required_specs = [s for s in specs if s.get("required", True)]
        optional_specs = [s for s in specs if not s.get("required", True)]
        req_ok = all(_match_spec(s, info, release) for s in required_specs)
        opt_ok = (not optional_specs) or any(_match_spec(s, info, release) for s in optional_specs)
        if req_ok and opt_ok:
            total += score
            breakdown.append((str(cf["name"]), score))
    return total, breakdown


def _split_terms(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]


def _restriction_rejections(
    title: str, restrictions: list[dict[str, Any]], asset_tag_ids: set[int]
) -> list[str]:
    reasons: list[str] = []
    lower_title = title.casefold()
    for rule in restrictions:
        if not rule.get("enabled", True):
            continue
        tag_id = rule.get("tag_id")
        if tag_id is not None and int(tag_id) not in asset_tag_ids:
            continue
        must = _split_terms(rule.get("must_contain"))
        if must and not any(term.casefold() in lower_title for term in must):
            reasons.append(f"Falta término obligatorio ({rule['name']})")
        for term in _split_terms(rule.get("must_not_contain")):
            if term.casefold() in lower_title:
                reasons.append(f"Contiene término excluido: {term}")
                break
    return reasons


def _effective_runtime_minutes(asset: dict[str, Any]) -> float | None:
    sec = asset.get("runtime_seconds")
    if sec and int(sec) > 0:
        return float(sec) / 60.0
    if asset.get("kind") == "series":
        return 45.0
    return 100.0


def evaluate_release(
    release: dict[str, Any],
    asset: dict[str, Any],
    profile: dict[str, Any],
    *,
    global_language: str = "any",
    custom_formats: list[dict[str, Any]] | None = None,
    restrictions: list[dict[str, Any]] | None = None,
    asset_tag_ids: set[int] | None = None,
    blocked_trackers: set[str] | None = None,
    failed_release_reasons: dict[int, str] | None = None,
    max_size_bytes: int = 0,
) -> ReleaseEvaluation:
    rid = int(release.get("id") or 0)
    title = str(release.get("title") or "")
    parsed = parse_release_info(title)
    quality = (release.get("quality") or parsed.quality or "unknown")
    audio = (release.get("audio") or parsed.audio or "unknown")
    info = ReleaseInfo(
        quality=quality,
        source_type=release.get("source_type") or parsed.source_type,
        codec=release.get("codec") or parsed.codec,
        hdr=release.get("hdr") or parsed.hdr,
        audio=audio,
        audio_codec=release.get("audio_codec") or parsed.audio_codec,
        audio_channels=release.get("audio_channels") or parsed.audio_channels,
        edition=release.get("edition") or parsed.edition,
        release_group=release.get("release_group") or parsed.release_group,
        proper_rank=int(release.get("proper_rank") if release.get("proper_rank") is not None else parsed.proper_rank),
        streaming_service=release.get("streaming_service") or parsed.streaming_service,
        flags=parsed.flags,
    )

    rejections: list[str] = []
    tracker = str(release.get("tracker") or "")
    if blocked_trackers and tracker in blocked_trackers:
        rejections.append("Tracker bloqueado")
    if failed_release_reasons and rid in failed_release_reasons:
        rejections.append(f"En blocklist ({failed_release_reasons[rid]})")
    if release.get("health") in ("dead", "blocked"):
        rejections.append(f"Salud: {release.get('health')}")

    if profile.get("block_cam", True) and info.source_type == "cam":
        rejections.append("Calidad CAM/Screener rechazada por el perfil")
    if profile.get("block_disc_images", True) and info.source_type == "brdisk":
        rejections.append("Imagen de disco (BRDISK/ISO) no compatible con streaming directo")

    allowed_q = [str(q) for q in (profile.get("allowed_qualities") or [])]
    if allowed_q and quality not in allowed_q:
        rejections.append(f"Calidad {quality} no permitida en {profile['name']}")

    size_bytes = int(release.get("size_bytes") or 0)
    runtime_min = _effective_runtime_minutes(asset)
    mb_per_min = (
        (float(size_bytes) / (1024.0 * 1024.0)) / runtime_min
        if size_bytes > 0 and runtime_min and runtime_min > 0
        else None
    )

    max_gb = float(profile.get("max_size_gb") or 0.0)
    if max_gb > 0 and size_bytes > max_gb * (1024 ** 3):
        rejections.append(
            f"Tamaño ({size_bytes / (1024 ** 3):.1f} GB) supera límite de {max_gb:.1f} GB"
        )

    max_mb_min = float(profile.get("max_mb_per_min") or 0.0)
    if max_mb_min > 0 and mb_per_min is not None and mb_per_min > max_mb_min:
        rejections.append(
            f"Bitrate ({mb_per_min:.0f} MB/min) supera máximo ({max_mb_min:.0f} MB/min)"
        )
    min_mb_min = float(profile.get("min_mb_per_min") or 0.0)
    if min_mb_min > 0 and mb_per_min is not None and mb_per_min < min_mb_min:
        rejections.append(
            f"Bitrate ({mb_per_min:.0f} MB/min) inferior al mínimo ({min_mb_min:.0f} MB/min)"
        )

    eff_lang = str(
        asset.get("language_override")
        or profile.get("language_preference")
        or "inherit"
    ).strip().lower()
    if eff_lang == "inherit":
        eff_lang = global_language
    lang_rank = language_settings.rank(
        audio,
        eff_lang,
        asset.get("original_language"),
        asset.get("original_country"),
    )
    if lang_rank is None:
        rejections.append(f"Audio ({audio}) excluido por preferencia ({eff_lang})")

    if restrictions:
        rejections.extend(
            _restriction_rejections(title, restrictions, asset_tag_ids or set())
        )

    score_map = profile.get("format_scores") or {}
    cf_score, breakdown = _custom_format_score(
        info, release, custom_formats or [], score_map
    )
    min_score = int(profile.get("min_score") or 0)
    if cf_score < min_score:
        rejections.append(f"Puntuación ({cf_score}) < mínimo ({min_score})")

    q_order = [str(q) for q in (profile.get("quality_order") or [])]
    q_idx = q_order.index(quality) if quality in q_order else len(q_order)

    cutoff_q = profile.get("cutoff_quality")
    if cutoff_q and cutoff_q in q_order:
        cutoff_idx = q_order.index(cutoff_q)
        quality_ok = q_idx <= cutoff_idx
    else:
        quality_ok = True
    cutoff_score = profile.get("cutoff_score")
    score_ok = True if cutoff_score is None else (cf_score >= int(cutoff_score))
    cutoff_met = bool(not rejections and quality_ok and score_ok)

    swarm_bad = (
        1
        if (release.get("swarm_failures") or 0) > 0
        and not release.get("swarm_started_ok")
        else 0
    )
    health_rank = {"alive": 0, "unknown": 1, "stale": 2}.get(
        str(release.get("health") or "unknown"), 3
    )
    eff_lang_sort = 9 if lang_rank is None else lang_rank
    prefer_smaller = bool(profile.get("prefer_smaller", True))
    size_sort = (
        (size_bytes if size_bytes > 0 else 10 ** 14)
        if prefer_smaller
        else -(size_bytes)
    )
    total_score = cf_score + info.proper_rank * 15
    sort_key = (
        0 if not rejections else 1,
        swarm_bad,
        health_rank,
        eff_lang_sort,
        q_idx,
        -total_score,
        size_sort,
        -int(release.get("seeders") or 0),
        -rid,
    )
    return ReleaseEvaluation(
        release_id=rid,
        allowed=not rejections,
        rejections=tuple(rejections),
        quality_tier=info.quality_tier,
        quality_idx=q_idx,
        language_rank=lang_rank,
        cf_score=cf_score,
        score_breakdown=tuple(breakdown),
        mb_per_min=mb_per_min,
        cutoff_met=cutoff_met,
        sort_key=sort_key,
    )


def load_evaluation_context(
    conn: sqlite3.Connection, db_path: str | Path, asset: dict[str, Any]
) -> dict[str, Any]:
    """Carga perfil efectivo, formatos, restricciones y tags para un asset."""
    has_profiles = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_profiles'"
        ).fetchone()
        is not None
    )
    profile = None
    custom_formats: list[dict[str, Any]] = []
    restrictions: list[dict[str, Any]] = []
    tag_ids: set[int] = set()
    if has_profiles:
        profile_id = asset.get("quality_profile_id")
        if profile_id is None and asset.get("kind") == "series" and asset.get("tmdb"):
            ss = conn.execute(
                "SELECT quality_profile_id, language_override FROM series_settings WHERE tmdb = ?",
                (int(asset["tmdb"]),),
            ).fetchone()
            if ss:
                profile_id = ss["quality_profile_id"]
                if not asset.get("language_override") and ss["language_override"]:
                    asset = {**asset, "language_override": ss["language_override"]}
        profile = profiles.get_profile(db_path, profile_id)
        custom_formats = profiles.list_custom_formats(db_path)
        restrictions = profiles.list_restrictions(db_path)
        if asset.get("tmdb"):
            tag_ids = {
                int(r["tag_id"])
                for r in conn.execute(
                    "SELECT tag_id FROM media_tags WHERE media_kind = ? AND tmdb = ?",
                    (asset["kind"], int(asset["tmdb"])),
                ).fetchall()
            }
    if profile is None:
        profile = profiles.DEFAULT_PROFILES[0]
        profile = {**profile, "format_scores": {}}
    blocked = {
        str(r["tracker"])
        for r in conn.execute(
            "SELECT tracker FROM tracker_policies WHERE enabled = 0"
        ).fetchall()
    }
    failed = {
        int(r["release_id"]): str(r["reason"] or "fallo")
        for r in conn.execute(
            "SELECT release_id, reason FROM stream_failures WHERE until > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        ).fetchall()
    }
    return {
        "asset": asset,
        "profile": profile,
        "global_language": language_settings.load(db_path),
        "custom_formats": custom_formats,
        "restrictions": restrictions,
        "asset_tag_ids": tag_ids,
        "blocked_trackers": blocked,
        "failed_release_reasons": failed,
    }
