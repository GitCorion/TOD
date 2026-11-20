"""Límites de descarga por categoría en qBittorrent.

qBittorrent no permite que un torrent supere el límite global. Para que
`ondemand` quede sin límite, el global debe estar a 0; este módulo limita
Radarr/Sonarr por torrent y deja `ondemand` ilimitado.

Los límites se expresan en KiB/s en la CLI y se convierten a bytes/s para la
API. `0` significa ilimitado.
"""

from __future__ import annotations

from typing import Any, Protocol

DEFAULT_RADARR_KBPS = 2500
DEFAULT_SONARR_KBPS = 2500
DEFAULT_ONDEMAND_KBPS = 0


class LimitsClient(Protocol):
    def torrents(self, category: str | None = None) -> list[dict]: ...
    def set_download_limit_many(
        self, info_hashes: list[str], limit: int
    ) -> None: ...


def limits_from_kbps(
    radarr_kbps: int,
    sonarr_kbps: int,
    ondemand_kbps: int = 0,
) -> dict[str, int]:
    sonarr_bytes = max(0, int(sonarr_kbps)) * 1024
    return {
        "radarr": max(0, int(radarr_kbps)) * 1024,
        "tv-sonarr": sonarr_bytes,
        "sonarr": sonarr_bytes,
        "ondemand": max(0, int(ondemand_kbps)) * 1024,
    }


def apply_limits(
    client: LimitsClient,
    limits: dict[str, int],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checked": 0,
        "already": 0,
        "updated": 0,
        "planned": [],
        "by_category": {},
        "errors": [],
        "dry_run": dry_run,
    }
    pending: dict[int, list[str]] = {}
    planned_counts: dict[str, int] = {}
    for torrent in client.torrents():
        category = torrent.get("category") or ""
        if category not in limits:
            continue
        info_hash = (torrent.get("hash") or "").lower()
        if not info_hash:
            continue
        desired = int(limits[category])
        current = int(torrent.get("dl_limit") or 0)
        report["checked"] += 1
        if current == desired:
            report["already"] += 1
            continue
        if dry_run:
            key = f"{category}:{desired}"
            planned_counts[key] = planned_counts.get(key, 0) + 1
            continue
        pending.setdefault(desired, []).append(info_hash)
        report["by_category"][category] = (
            report["by_category"].get(category, 0) + 1
        )
    for key, count in sorted(planned_counts.items()):
        category, desired = key.rsplit(":", 1)
        report["planned"].append(
            {
                "category": category,
                "limit_bytes": int(desired),
                "torrents": count,
            }
        )
    for desired, hashes in pending.items():
        try:
            client.set_download_limit_many(hashes, desired)
            report["updated"] += len(hashes)
        except Exception as exc:
            report["errors"].append(
                f"{len(hashes)} torrents a {desired}: {str(exc)[:200]}"
            )
    return report
