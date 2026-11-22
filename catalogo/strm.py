"""Genera .strm multi-versión como /mnt/catalogo.

Por cada título:
  - Title (Year).strm                 -> /play?key=... (mejor fuente)
  - Title (Year) - 1080p Cast.strm    -> /play?key=...&release=<id>
  - Title (Year) - 4K ESP.strm        -> /play?key=...&release=<id>

No escribe en Jellyfin. Solo en la carpeta de salida indicada.
"""

from __future__ import annotations

import re
from contextlib import closing
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .db import connect
from .health import publishable
from .identity import play_url
from .library_index import build_library_index, in_library

INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(value: str, fallback: str = "Sin titulo") -> str:
    text = INVALID.sub(" ", value or "").strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:180] or fallback


def _title(asset: dict) -> str:
    return safe_name(asset["title"] or asset["key"])


def strm_path(out_dir: Path, asset: dict) -> Path:
    title = _title(asset)
    suffix = f" ({asset['year']})" if asset["year"] else ""
    if asset["kind"] == "movie":
        return out_dir / "movies" / f"{title}{suffix}" / f"{title}{suffix}.strm"
    season = int(asset["season"] or 0)
    episode = int(asset["episode"] or 0)
    return (
        out_dir
        / "series"
        / f"{title}{suffix}"
        / f"Season {season:02d}"
        / f"{title} S{season:02d}E{episode:02d}.strm"
    )


def version_path(out_dir: Path, asset: dict, label: str) -> Path:
    default = strm_path(out_dir, asset)
    return default.with_name(f"{default.stem} - {safe_name(label)}.strm")


def _group_writable(path: Path, *, stop: Path) -> None:
    """Da escritura al grupo en directorios nuevos (Jellyfin guarda .nfo).

    No falla si el sistema de ficheros no lo permite: es una mejora, no un
    requisito para exportar.
    """
    current = path
    while True:
        try:
            mode = current.stat().st_mode
            current.chmod(mode | 0o070)
        except OSError:
            return
        if current == stop or current.parent == current:
            return
        current = current.parent


def write_strm(path: Path, content: str, *, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _group_writable(path.parent, stop=root)
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(path.stat().st_mode | 0o060)
    except OSError:
        pass


def _nfo(asset: dict) -> tuple[Path, str] | None:
    title = xml_escape(str(asset.get("title") or ""))
    year = asset.get("year")
    tmdb = asset.get("tmdb")
    if asset["kind"] == "movie":
        default = strm_path(Path("."), asset)
        path = default.with_suffix(".nfo")
        body = [
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
            "<movie>",
            f"  <title>{title}</title>",
        ]
        if year:
            body.append(f"  <year>{int(year)}</year>")
        if tmdb:
            body.append(f"  <tmdbid>{int(tmdb)}</tmdbid>")
        body.append("</movie>")
        return path, "\n".join(body) + "\n"
    default = strm_path(Path("."), asset)
    path = default.parent.parent / "tvshow.nfo"
    body = [
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        "<tvshow>",
        f"  <title>{title}</title>",
    ]
    if year:
        body.append(f"  <year>{int(year)}</year>")
    if tmdb:
        body.append(f"  <tmdbid>{int(tmdb)}</tmdbid>")
    if asset.get("tvdb_id"):
        body.append(f"  <tvdbid>{int(asset['tvdb_id'])}</tvdbid>")
    body.append("</tvshow>")
    return path, "\n".join(body) + "\n"


def _quality(value: str | None) -> str:
    text = (value or "unknown").lower()
    return {"2160p": "4K", "uhd": "4K", "4k": "4K"}.get(text, text)


def _audio(value: str | None) -> str:
    text = (value or "unknown").lower()
    return {
        "castellano": "Cast",
        "spanish_unverified": "ESP",
        "espanol": "ESP",
        "latino": "Lat",
        "vose": "VOSE",
        "unknown": "VO",
    }.get(text, text)


def _available_release_ids(conn) -> set[int]:
    """Releases con un fichero local/archivo que existe de verdad."""
    rows = conn.execute(
        """
        SELECT release_id, local_path, library_path
        FROM torrent_files
        WHERE release_id IS NOT NULL
          AND state NOT IN ('evicted', 'missing')
        """
    ).fetchall()
    result: set[int] = set()
    for row in rows:
        for column in ("local_path", "library_path"):
            path = row[column]
            if path and Path(path).is_file():
                result.add(int(row["release_id"]))
                break
    return result


def _versions(
    asset: dict,
    releases: list,
    available_ids: set[int] | None = None,
    *,
    versions_filter: str = "available",
    min_seeders: int = 1,
) -> list[tuple[dict, str]]:
    """Devuelve las versiones que deben tener fichero `.strm` propio.

    ``available`` (por defecto) solo exporta versiones que ya se pueden
    reproducir: fichero local/archivo presente o release cacheado en
    Real-Debrid. Así Jellyfin no sondea `.strm` que devolverían 403.
    ``alive`` exporta también releases vivos con seeds; ``all`` mantiene el
    comportamiento antiguo.
    """
    available_ids = available_ids or set()
    used: set[str] = set()
    result: list[tuple[dict, str]] = []
    for release in releases:
        release_id = int(release["id"])
        is_available = release_id in available_ids or (
            release["debrid_cached"] and release["debrid_link"]
        )
        if versions_filter == "available":
            decision_allowed = bool(is_available)
        elif versions_filter == "alive":
            if is_available:
                decision_allowed = True
            else:
                decision = publishable(
                    source_kind="torrent",
                    health=release["health"],
                    seeders=release["seeders"],
                    require_health=True,
                    min_seeders=min_seeders,
                )
                decision_allowed = decision.ok
        elif versions_filter == "all":
            if is_available:
                decision_allowed = True
            else:
                decision = publishable(
                    source_kind="torrent",
                    health=release["health"],
                    seeders=release["seeders"],
                    require_health=False,
                    min_seeders=min_seeders,
                )
                decision_allowed = decision.ok
        else:
            raise ValueError(f"versions_filter no válido: {versions_filter}")
        if not decision_allowed:
            continue
        base = f"{_quality(release['quality'])} {_audio(release['audio'])}".strip()
        label = safe_name(base, "version")
        if label in used:
            tracker = safe_name(
                (release["tracker"] or "").replace(" (API)", ""), "tracker"
            )
            label = safe_name(f"{base} - {tracker}", "version")
        if label in used:
            label = f"{label} - {release['id']}"
        while label in used:
            label = f"{label} {release['id']}"
        used.add(label)
        result.append((release, label))
    return result


def export_strm(
    db_path: Path | str,
    out_dir: Path | str,
    *,
    base_url: str = "http://127.0.0.1:8773",
    limit: int | None = None,
    key: str | None = None,
    require_health: bool = False,
    min_seeders: int = 1,
    versions_filter: str = "available",
    prune: bool = False,
    existing_only: bool = False,
    year: int | None = None,
    kind: str | None = None,
    skip_library: bool = False,
    radarr=None,
    sonarr=None,
) -> dict:
    if versions_filter not in ("available", "alive", "all"):
        raise ValueError(f"versions_filter no válido: {versions_filter}")
    if prune and (key or limit):
        raise ValueError("--prune solo se permite en una exportación completa")
    out = Path(out_dir)

    def ensure_nfo(asset: dict) -> None:
        data = _nfo(asset)
        if data is None:
            return
        relative, content = data
        path = out / relative
        if path in nfo_done:
            return
        nfo_done.add(path)
        # Radarr/Sonarr ya pueden haber escrito un NFO completo. No se
        # sobrescribe: el de TOD es solo un respaldo para emparejar.
        if path.is_file():
            return
        write_strm(path, content, root=out)

    written = 0
    unchanged = 0
    skipped = 0
    versions_written = 0
    pruned = 0
    written_paths: list[str] = []
    nfo_done: set[Path] = set()
    expected: set[Path] = set()
    skipped_reasons: dict[str, int] = {}
    library = build_library_index(radarr, sonarr) if skip_library else None
    conditions = [
        """(
            EXISTS (SELECT 1 FROM releases r WHERE r.asset_id = a.id)
            OR EXISTS (
                SELECT 1 FROM torrent_files f
                WHERE f.asset_id = a.id
                  AND f.state IN ('ready', 'archived', 'seeding', 'complete')
            )
            OR EXISTS (
                SELECT 1 FROM library_files lf
                WHERE lf.kind = a.kind AND lf.tmdb = a.tmdb
                  AND (
                      a.kind = 'movie'
                      OR (lf.season = a.season AND lf.episode = a.episode)
                  )
            )
        )"""
    ]
    params: list[object] = []
    if key:
        conditions.append("(a.key = ? OR a.media_id = ?)")
        params.extend([key, key])
    if year:
        conditions.append("a.year = ?")
        params.append(year)
    if kind:
        conditions.append("a.kind = ?")
        params.append(kind)
    assets_query = (
        "SELECT * FROM media_items a WHERE "
        + " AND ".join(conditions)
        + " ORDER BY a.id"
    )
    if limit:
        assets_query += " LIMIT ?"
        params.append(limit)

    with closing(connect(db_path)) as conn:
        available_ids = _available_release_ids(conn)
        local_assets = {
            int(row["asset_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT asset_id FROM torrent_files
                WHERE asset_id IS NOT NULL
                  AND state IN ('ready', 'archived', 'seeding', 'complete')
                  AND (local_path IS NOT NULL OR library_path IS NOT NULL)
                """
            ).fetchall()
        }
        assets = conn.execute(assets_query, params).fetchall()
        if existing_only:
            assets = [
                asset for asset in assets
                if strm_path(out, dict(asset)).is_file()
            ]
        all_releases = {}
        library_available: dict[int, bool] = {}
        for asset in assets:
            asset_id = int(asset["id"])
            all_releases[asset_id] = conn.execute(
                """
                SELECT * FROM releases
                WHERE asset_id = ?
                ORDER BY
                    CASE health WHEN 'alive' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,
                    COALESCE(size_bytes, 999999999999) ASC,
                    COALESCE(seeders, 0) DESC,
                    id DESC
                """,
                (asset_id,),
            ).fetchall()
            library_available[asset_id] = bool(
                conn.execute(
                    """
                    SELECT 1 FROM library_files lf
                    WHERE lf.kind = ? AND lf.tmdb = ?
                      AND (
                          ? = 'movie'
                          OR (lf.season = ? AND lf.episode = ?)
                      )
                    LIMIT 1
                    """,
                    (
                        asset["kind"],
                        asset["tmdb"],
                        asset["kind"],
                        int(asset["season"] or 0),
                        int(asset["episode"] or 0),
                    ),
                ).fetchone()
            )

    for asset_row in assets:
        asset = dict(asset_row)
        if library is not None and in_library(library, asset):
            skipped += 1
            skipped_reasons["in_library"] = (
                skipped_reasons.get("in_library", 0) + 1
            )
            continue
        releases = [
            release for release in all_releases[int(asset["id"])]
            if release["health"] in ("alive", "unknown", "stale")
            or int(release["id"]) in available_ids
        ]
        playable = _versions(
            asset,
            releases,
            available_ids,
            versions_filter="all",
            min_seeders=min_seeders,
        )
        # Un .strm solo se publica si hay algo que reproducir de verdad:
        # fichero local/archivado, debrid cacheado o un release VIVO con
        # seeds. Los releases unknown/stale/dead no crean entradas muertas
        # en Jellyfin; el catálogo conserva el título para reintentarlo.
        playable = [
            (release, label) for release, label in playable
            if int(release["id"]) in available_ids
            or (release["debrid_cached"] and release["debrid_link"])
            or (
                release["health"] == "alive"
                and int(release["seeders"] or 0) >= min_seeders
            )
        ]
        if not playable:
            if (
                not library_available.get(int(asset["id"]), False)
                and int(asset["id"]) not in local_assets
            ):
                skipped += 1
                reason = "no_playable" if releases else "no_releases"
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                continue
            # Hay una copia local o de biblioteca aunque no haya release:
            # el resolver la sirve directamente.
            default = strm_path(out, asset)
            expected.add(default)
            content = play_url(asset["key"], base_url) + "\n"
            if not default.exists() or default.read_text(encoding="utf-8") != content:
                write_strm(default, content, root=out)
                written += 1
                written_paths.append(str(default))
            else:
                unchanged += 1
            ensure_nfo(asset)
            continue

        versions = _versions(
            asset,
            releases,
            available_ids,
            versions_filter=versions_filter,
            min_seeders=min_seeders,
        )

        default = strm_path(out, asset)
        expected.add(default)
        content = play_url(asset["key"], base_url) + "\n"
        if not default.exists() or default.read_text(encoding="utf-8") != content:
            write_strm(default, content, root=out)
            written += 1
            written_paths.append(str(default))
        else:
            unchanged += 1
        ensure_nfo(asset)

        if len(versions) > 1:
            for release, label in versions:
                path = version_path(out, asset, label)
                expected.add(path)
                content = (
                    play_url(asset["key"], base_url)
                    + f"&release={int(release['id'])}\n"
                )
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    write_strm(path, content, root=out)
                    versions_written += 1
                    written_paths.append(str(path))

    if prune:
        for path in sorted(out.rglob("*.strm")):
            if path in expected:
                continue
            path.unlink()
            pruned += 1
            parent = path.parent
            while parent != out and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    return {
        "out": str(out),
        "assets": len(assets),
        "written": written,
        "versions_written": versions_written,
        "written_paths": written_paths,
        "unchanged": unchanged,
        "skipped": skipped,
        "skipped_reasons": skipped_reasons,
        "pruned": pruned,
        "base_url": base_url,
        "require_health": require_health,
        "min_seeders": min_seeders,
        "versions_filter": versions_filter,
        "existing_only": existing_only,
        "year": year,
        "kind": kind,
        "skip_library": skip_library,
    }
