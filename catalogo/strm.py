"""Genera .strm multi-versión como /mnt/catalogo.

Por cada título:
  - Title (Year).strm                 -> /play?key=... (mejor fuente)
  - Title (Year) - 1080p Cast.strm    -> /play?key=...&release=<id>
  - Title (Year) - 4K ESP.strm        -> /play?key=...&release=<id>

No escribe en Jellyfin. Solo en la carpeta de salida indicada.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from contextlib import closing
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from . import language_settings
from . import naming as naming_mod
from .app_settings import _as_bool, _read_overrides
from .config import Config
from .db import connect
from .health import publishable
from .identity import play_url
from .library_index import build_library_index, in_library
from .resolver import Resolver

INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(value: str, fallback: str = "Sin titulo") -> str:
    text = INVALID.sub(" ", value or "").strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:180] or fallback


def _title(asset: dict) -> str:
    return safe_name(asset["title"] or asset["key"])


def strm_path(out_dir: Path, asset: dict, *, naming_cfg=None,
              root_folders_by_id=None, series_settings_map=None) -> Path:
    """Ruta del .strm principal. Delega a naming_mod si hay configuración."""
    if naming_cfg is not None:
        return naming_mod.build_asset_strm_path(
            out_dir, asset, cfg=naming_cfg,
            root_folders_by_id=root_folders_by_id,
            series_settings_map=series_settings_map,
        )
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


def version_path(out_dir: Path, asset: dict, label: str, *, naming_cfg=None,
                 root_folders_by_id=None, series_settings_map=None) -> Path:
    default = strm_path(out_dir, asset, naming_cfg=naming_cfg,
                        root_folders_by_id=root_folders_by_id,
                        series_settings_map=series_settings_map)
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


def _rich_nfo_tags(asset: dict) -> list[str]:
    """Campos NFO extra cuando rich_nfo está activado."""
    tags: list[str] = []
    imdb = asset.get("imdb_id") or asset.get("imdb") or ""
    if imdb:
        tags.append(f'  <uniqueid type="imdb">{xml_escape(str(imdb))}</uniqueid>')
    genres = str(asset.get("genres") or "")
    for genre in genres.split(","):
        g = genre.strip()
        if g:
            tags.append(f"  <genre>{xml_escape(g)}</genre>")
    overview = str(asset.get("overview") or "").strip()
    if overview:
        tags.append(f"  <plot>{xml_escape(overview[:2000])}</plot>")
    runtime = asset.get("runtime_seconds")
    if runtime and int(runtime) > 0:
        tags.append(f"  <runtime>{int(runtime) // 60}</runtime>")
    vote = asset.get("vote_average")
    if vote:
        try:
            tags.append(f"  <rating>{float(vote):.1f}</rating>")
        except (TypeError, ValueError):
            pass
    cert = str(asset.get("certification") or "").strip()
    if cert:
        tags.append(f"  <mpaa>{xml_escape(cert)}</mpaa>")
    orig_title = str(asset.get("original_title") or "").strip()
    if orig_title:
        tags.append(f"  <originaltitle>{xml_escape(orig_title)}</originaltitle>")
    return tags


def _safe_write_strm(path: Path, content: str, *, root: Path) -> bool:
    try:
        write_strm(path, content, root=root)
        return True
    except OSError:
        return False


def direct_local_enabled(db_path: Path | str) -> bool:
    """¿Los .strm nuevos pueden apuntar al fichero local directamente?"""
    overrides = _read_overrides(db_path)
    if "strm_direct_local" in overrides:
        return _as_bool(overrides["strm_direct_local"])
    return Config.from_env().strm_direct_local


def _direct_path(
    resolver: Resolver, asset: dict, release_id: int | None = None
) -> str | None:
    """Ruta real y legible del fichero local, o None si hay que usar proxy."""
    try:
        resolution = (
            resolver.resolve_release(str(asset["key"]), int(release_id))
            if release_id is not None
            else resolver.resolve(str(asset["key"]))
        )
    except Exception:
        return None
    if resolution.source not in ("library", "local_seed") or not resolution.path:
        return None
    path = Path(resolution.path).expanduser()
    if not path.is_absolute() or not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0 or not os.access(str(path), os.R_OK):
        return None
    return str(path)


def strm_content(
    key: str,
    base_url: str,
    direct_path: str | None = None,
    *,
    download: bool = False,
) -> str:
    """Contenido de un .strm: ruta directa si hay fichero, si no proxy."""
    if direct_path:
        return f"{direct_path}\n"
    url = play_url(key, base_url)
    if download:
        url += "&download=1"
    return f"{url}\n"


def direct_path_map(
    db_path: Path | str,
) -> dict[str, tuple[str, int | None]]:
    """Ruta de fichero -> (key, release_id) para los .strm directos."""
    result: dict[str, tuple[str, int | None]] = {}
    try:
        with closing(connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT m.key, f.release_id, f.local_path, f.library_path "
                "FROM torrent_files f "
                "JOIN media_items m ON m.id = f.asset_id "
                "ORDER BY f.id DESC"
            ).fetchall()
    except Exception:
        return result
    for row in rows:
        value = row["release_id"]
        identity = (row["key"], int(value) if value is not None else None)
        for column in ("local_path", "library_path"):
            raw = row[column]
            if not raw:
                continue
            result[str(raw)] = identity
            try:
                result[str(Path(raw).resolve(strict=False))] = identity
            except (OSError, RuntimeError):
                pass
    return result


def strm_identity(
    db_path: Path | str,
    path: Path | str,
    *,
    path_map: dict[str, tuple[str, int | None]] | None = None,
) -> tuple[str | None, int | None]:
    """Extrae (key, release_id) de un .strm, sea URL del proxy o ruta directa.

    Los consumidores del catálogo (inventario, leases, limpieza) asumían que
    todo .strm contenía `/play?key=...`. Con reproducción directa el contenido
    es la ruta del fichero: se mapea de vuelta a través de `torrent_files`.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None, None
    if not text:
        return None, None
    if text.startswith(("http://", "https://", "/play?", "/resolve?")):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        key = (params.get("key") or [None])[0]
        value = (params.get("release") or [None])[0]
        try:
            release_id = int(value) if value else None
        except (TypeError, ValueError):
            release_id = None
        return key, release_id
    if path_map is None:
        path_map = direct_path_map(db_path)
    raw = Path(text).expanduser()
    identity = path_map.get(str(raw))
    if identity is None:
        try:
            identity = path_map.get(str(raw.resolve(strict=False)))
        except (OSError, RuntimeError):
            identity = None
    return identity or (None, None)


def _nfo_path_for(
    default_strm: Path, kind: str, *, use_season_folders: bool = True
) -> Path:
    if kind == "movie":
        return default_strm.with_suffix(".nfo")
    parent = default_strm.parent.parent if use_season_folders else default_strm.parent
    return parent / "tvshow.nfo"


def _nfo(asset: dict, *, rich: bool = False) -> tuple[Path, str] | None:
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
        if rich:
            body.extend(_rich_nfo_tags(asset))
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
    if rich:
        body.extend(_rich_nfo_tags(asset))
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


UNBLOCKED_RELEASE_SQL = (
    "r.health <> 'blocked' "
    "AND NOT EXISTS ("
    "SELECT 1 FROM tracker_policies tp "
    "WHERE tp.tracker = r.tracker AND tp.enabled = 0"
    ")"
)


def _remove_empty_parents(parent: Path, root: Path) -> None:
    stop = {root / "movies", root / "series"}
    while (
        parent != root
        and parent not in stop
        and parent.is_dir()
        and not any(parent.iterdir())
    ):
        parent.rmdir()
        parent = parent.parent


def _available_release_ids(conn) -> set[int]:
    """Releases con un fichero local/archivo presente según qBittorrent.

    El estado de `torrent_files` lo mantiene `qbit-sync`; no se recorre el
    disco. En una biblioteca de red eso duplicaría el trabajo del *arr.
    """
    rows = conn.execute(
        """
        SELECT release_id, local_path, library_path
        FROM torrent_files
        WHERE release_id IS NOT NULL
          AND state NOT IN ('evicted', 'missing')
        """
    ).fetchall()
    return {
        int(release_id)
        for release_id, local_path, library_path in rows
        if local_path or library_path
    }



def _available_pack_by_asset(conn) -> tuple[dict[int, set[str]], dict[int, list[Any]]]:
    """Hashes y releases de pack disponibles por asset_id según torrent_files."""
    rows = conn.execute(
        """
        SELECT f.asset_id,
               f.local_path,
               f.library_path,
               COALESCE(NULLIF(t.info_hash, ''), r.info_hash) AS eff_hash,
               r.*
        FROM torrent_files f
        LEFT JOIN torrents t ON t.id = f.torrent_id
        LEFT JOIN releases r ON r.id = f.release_id
        WHERE f.asset_id IS NOT NULL
          AND f.state NOT IN ('evicted', 'missing')
        """
    ).fetchall()
    hashes: dict[int, set[str]] = {}
    rels_by_asset: dict[int, dict[int, Any]] = {}
    for row in rows:
        if not (row["local_path"] or row["library_path"]):
            continue
        aid = int(row["asset_id"])
        eh = row["eff_hash"]
        if eh:
            hashes.setdefault(aid, set()).add(str(eh).strip().lower())
        if row["id"] is not None:
            rels_by_asset.setdefault(aid, {})[int(row["id"])] = row
    return hashes, {aid: list(m.values()) for aid, m in rels_by_asset.items()}

def _versions(
    asset: dict,
    releases: list,
    available_ids: set[int] | None = None,
    *,
    versions_filter: str = "available",
    min_seeders: int = 1,
    preference: str = "any",
    pinned_release_id: int | None = None,
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
        is_available = release_id in available_ids
        is_pinned = pinned_release_id is not None and release_id == pinned_release_id
        if (
            not is_available
            and not is_pinned
            and language_settings.rank(
                release["audio"],
                preference,
                asset.get("original_language"),
                asset.get("original_country"),
            )
            is None
        ):
            continue
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
    direct_local: bool | None = None,
) -> dict:
    if versions_filter not in ("available", "alive", "all"):
        raise ValueError(f"versions_filter no válido: {versions_filter}")
    if prune and (key or limit):
        raise ValueError("--prune solo se permite en una exportación completa")
    out = Path(out_dir)
    direct = direct_local_enabled(db_path) if direct_local is None else direct_local
    resolver = Resolver(db_path) if direct else None
    preference = language_settings.load(db_path)
    # --- Fase 3: naming config ---
    try:
        _naming_cfg = naming_mod.load_naming_settings(db_path)
        _root_folders = {rf["id"]: rf for rf in naming_mod.list_root_folders(db_path)}
        _ss_map: dict[int, dict] = {}
        with closing(connect(db_path)) as _nc:
            if _nc.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_settings'"
            ).fetchone():
                for _r in _nc.execute("SELECT * FROM series_settings").fetchall():
                    _ss_map[int(_r["tmdb"])] = dict(_r)
        _rich_nfo = _naming_cfg.get("rich_nfo", False)
    except Exception:
        _naming_cfg = None
        _root_folders = {}
        _ss_map = {}
        _rich_nfo = False
    def _strm(asset):
        return strm_path(out, asset, naming_cfg=_naming_cfg,
                         root_folders_by_id=_root_folders,
                         series_settings_map=_ss_map)


    nfo_done: set[Path] = set()

    def ensure_nfo(asset: dict, default_strm: Path | None = None) -> None:
        data = _nfo(asset, rich=_rich_nfo)
        if data is None:
            return
        relative, content = data
        if default_strm is not None:
            use_sf = bool(_naming_cfg.get("use_season_folders", True)) if _naming_cfg else True
            path = _nfo_path_for(default_strm, asset["kind"], use_season_folders=use_sf)
        else:
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
    expected: set[Path] = set()
    skipped_reasons: dict[str, int] = {}
    library = build_library_index(radarr, sonarr) if skip_library else None
    conditions = [
        f"""(
            EXISTS (SELECT 1 FROM releases r WHERE r.asset_id = a.id AND {UNBLOCKED_RELEASE_SQL})
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
            OR EXISTS (
                SELECT 1 FROM torrent_files f
                JOIN torrents t ON t.id = f.torrent_id
                WHERE f.asset_id = a.id
                  AND t.category IN ('radarr', 'tv-sonarr')
                  AND t.state <> 'removed'
                  AND f.state NOT IN ('evicted', 'missing')
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
        available_hashes, available_pack_rels = _available_pack_by_asset(conn)
        pinned_targets: dict[int, int] = {}
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='play_targets'"
        ).fetchone():
            pinned_targets = {
                int(r["asset_id"]): int(r["release_id"])
                for r in conn.execute(
                    "SELECT asset_id, release_id FROM play_targets "
                    "WHERE source_kind = 'torrent' AND release_id IS NOT NULL"
                ).fetchall()
            }
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
        # Descargas del *arr en cola o a medias: publicamos .strm para que
        # se puedan pedir desde Jellyfin; al reproducirlas TOD las adopta.
        arr_assets = {
            int(row["asset_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT f.asset_id
                FROM torrent_files f
                JOIN torrents t ON t.id = f.torrent_id
                WHERE f.asset_id IS NOT NULL
                  AND t.category IN ('radarr', 'tv-sonarr')
                  AND t.state <> 'removed'
                  AND f.state NOT IN ('evicted', 'missing')
                """
            ).fetchall()
        }
        assets = conn.execute(assets_query, params).fetchall()
        if existing_only:
            assets = [
                asset for asset in assets
                if strm_path(out, dict(asset), naming_cfg=_naming_cfg, root_folders_by_id=_root_folders, series_settings_map=_ss_map).is_file()
            ]
        all_releases = {}
        library_available: dict[int, bool] = {}
        for asset in assets:
            asset_id = int(asset["id"])
            all_releases[asset_id] = conn.execute(
                f"""
                SELECT r.* FROM releases r
                WHERE r.asset_id = ?
                  AND {UNBLOCKED_RELEASE_SQL}
                ORDER BY
                    CASE r.health WHEN 'alive' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,
                    COALESCE(r.size_bytes, 999999999999) ASC,
                    COALESCE(r.seeders, 0) DESC,
                    r.id DESC
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
        asset_id = int(asset["id"])
        asset_hashes = available_hashes.get(asset_id, set())
        pack_rels = available_pack_rels.get(asset_id, [])
        pack_keys = {
            (str(pr["tracker"] or "").lower(), str(pr["title"] or "").strip().lower())
            for pr in pack_rels
            if pr["title"]
        }
        asset_available_ids = set(available_ids)
        base_releases = list(all_releases[asset_id])
        seen_ids = {int(r["id"]) for r in base_releases}
        for release in base_releases:
            rel_hash = (release["info_hash"] or "").strip().lower()
            rel_key = (str(release["tracker"] or "").lower(), str(release["title"] or "").strip().lower())
            if (rel_hash and rel_hash in asset_hashes) or (rel_key in pack_keys):
                asset_available_ids.add(int(release["id"]))
        for pr in pack_rels:
            pr_id = int(pr["id"])
            pr_key = (str(pr["tracker"] or "").lower(), str(pr["title"] or "").strip().lower())
            if pr_id not in seen_ids and not any(
                (str(r["tracker"] or "").lower(), str(r["title"] or "").strip().lower()) == pr_key
                for r in base_releases
            ):
                base_releases.append(pr)
                seen_ids.add(pr_id)
                asset_available_ids.add(pr_id)
        releases = [
            release for release in base_releases
            if release["health"] in ("alive", "unknown", "stale")
            or int(release["id"]) in asset_available_ids
        ]
        playable = _versions(
            asset,
            releases,
            asset_available_ids,
            versions_filter="all",
            min_seeders=min_seeders,
            preference=preference,
            pinned_release_id=pinned_targets.get(asset_id),
        )
        # Un .strm solo se publica si hay algo que reproducir de verdad:
        # fichero local/archivado o un release VIVO con seeds. Los releases
        # unknown/stale/dead no crean entradas muertas en Jellyfin; el
        # catálogo conserva el título para reintentarlo.
        playable = [
            (release, label) for release, label in playable
            if int(release["id"]) in asset_available_ids
            or (
                release["health"] == "alive"
                and int(release["seeders"] or 0) >= min_seeders
            )
        ]
        if not playable:
            if (
                not library_available.get(int(asset["id"]), False)
                and int(asset["id"]) not in local_assets
                and int(asset["id"]) not in arr_assets
            ):
                skipped += 1
                reason = "no_playable" if releases else "no_releases"
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                continue
            # Hay una copia local o de biblioteca aunque no haya release:
            # el resolver la sirve directamente.
            default = _strm(asset)
            expected.add(default)
            path_directa = _direct_path(resolver, asset) if direct else None
            content = strm_content(asset["key"], base_url, path_directa)
            if not default.exists() or default.read_text(encoding="utf-8") != content:
                _safe_write_strm(default, content, root=out)
                written += 1
                written_paths.append(str(default))
            else:
                unchanged += 1
            ensure_nfo(asset, default)
            continue

        versions = _versions(
            asset,
            releases,
            asset_available_ids,
            versions_filter=versions_filter,
            min_seeders=min_seeders,
            preference=preference,
        )

        default = _strm(asset)
        expected.add(default)
        path_directa = _direct_path(resolver, asset) if direct else None
        content = strm_content(asset["key"], base_url, path_directa)
        if not default.exists() or default.read_text(encoding="utf-8") != content:
            _safe_write_strm(default, content, root=out)
            written += 1
            written_paths.append(str(default))
        else:
            unchanged += 1
        ensure_nfo(asset, default)

        if len(versions) > 1:
            for release, label in versions:
                path = version_path(out, asset, label, naming_cfg=_naming_cfg, root_folders_by_id=_root_folders, series_settings_map=_ss_map)
                expected.add(path)
                release_id = int(release["id"])
                path_directa = (
                    _direct_path(resolver, asset, release_id) if direct else None
                )
                content = (
                    strm_content(asset["key"], base_url, path_directa)
                    if path_directa
                    else play_url(asset["key"], base_url)
                    + f"&release={release_id}\n"
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
            _remove_empty_parents(path.parent, out)

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
        "direct_local": direct,
        "require_health": require_health,
        "min_seeders": min_seeders,
        "versions_filter": versions_filter,
        "existing_only": existing_only,
        "year": year,
        "kind": kind,
        "skip_library": skip_library,
    }


def refresh_direct_strm(
    db_path: Path | str,
    out_dir: Path | str,
    keys,
    *,
    base_url: str | None = None,
    direct_local: bool | None = None,
) -> dict:
    """Reescribe los .strm de títulos cuyo fichero local cambió de ruta."""
    enabled = (
        direct_local_enabled(db_path) if direct_local is None else direct_local
    )
    if not enabled:
        return {"enabled": False, "refreshed": []}
    from . import strm_inventory

    refreshes: list[str] = []
    errors: list[str] = []
    url = base_url or Config.from_env().playback_base_url
    for key in dict.fromkeys(str(item) for item in keys if item):
        try:
            export_strm(
                db_path, out_dir, key=key, base_url=url, direct_local=enabled
            )
            refreshes.append(key)
        except Exception as exc:
            errors.append(f"{key}: {str(exc)[:160]}")
    if refreshes:
        strm_inventory.invalidate(out_dir)
    return {"enabled": True, "refreshed": refreshes, "errors": errors}


def prune_key(
    db_path: Path | str, out_dir: Path | str, key: str
) -> list[str]:
    """Borra los .strm (principal y versiones) de una clave concreta.

    Se usa cuando el *arr ya ha importado el fichero y su biblioteca es la
    fuente: el .strm de TOD deja de ser necesario y duplicaría la entrada.
    """
    with closing(connect(db_path)) as conn:
        asset = conn.execute(
            "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
            (key, key),
        ).fetchone()
    if asset is None:
        return []
    root = Path(out_dir)
    default = strm_path(root, dict(asset))
    candidates: set[Path] = {default}
    if default.parent.is_dir():
        for path in default.parent.glob("*.strm"):
            found_key, _ = strm_identity(db_path, path)
            if found_key == key or path == default:
                candidates.add(path)
    removed: list[str] = []
    for path in candidates:
        if path.is_file():
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                continue
    if removed:
        _remove_empty_parents(default.parent, root)
    return removed


def _has_playable_source(
    conn, asset: dict, *, available_ids: set[int], min_seeders: int = 1
) -> bool:
    asset_id = int(asset["id"])
    rows = conn.execute(
        f"""
        SELECT r.id, r.health, r.seeders FROM releases r
        WHERE r.asset_id = ? AND {UNBLOCKED_RELEASE_SQL}
        """,
        (asset_id,),
    ).fetchall()
    for r in rows:
        if int(r["id"]) in available_ids or (
            r["health"] == "alive" and int(r["seeders"] or 0) >= min_seeders
        ):
            return True
    if conn.execute(
        """
        SELECT 1 FROM torrent_files
        WHERE asset_id = ?
          AND state IN ('ready', 'archived', 'seeding', 'complete')
          AND (local_path IS NOT NULL OR library_path IS NOT NULL)
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone():
        return True
    if conn.execute(
        """
        SELECT 1 FROM torrent_files f
        JOIN torrents t ON t.id = f.torrent_id
        WHERE f.asset_id = ?
          AND t.category IN ('radarr', 'tv-sonarr')
          AND t.state <> 'removed'
          AND f.state NOT IN ('evicted', 'missing')
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone():
        return True
    return bool(
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


def prune_unplayable_keys(
    db_path: Path | str,
    out_dir: Path | str,
    keys,
    *,
    min_seeders: int = 1,
) -> list[str]:
    """Borra los `.strm` de las claves que se quedaron sin ninguna fuente reproducible."""
    unique_keys = list(dict.fromkeys(str(k) for k in keys if k))
    root = Path(out_dir)
    if not unique_keys or not root.is_dir():
        return []
    to_prune: list[str] = []
    with closing(connect(db_path)) as conn:
        available_ids = _available_release_ids(conn)
        for key in unique_keys:
            asset = conn.execute(
                "SELECT * FROM media_items WHERE key = ? OR media_id = ? LIMIT 1",
                (key, key),
            ).fetchone()
            if asset is None:
                continue
            if not _has_playable_source(
                conn, dict(asset), available_ids=available_ids, min_seeders=min_seeders
            ):
                to_prune.append(key)
    removed: list[str] = []
    for key in to_prune:
        removed.extend(prune_key(db_path, root, key))
    return removed
