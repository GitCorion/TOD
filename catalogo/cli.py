"""CLI de TOD: administración, migraciones y reproducción."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .admission import admit, cancel_intent, list_intents
from .abandon import abandon_idle
from .arr import ArrError, RadarrClient, SonarrClient
from .backfill import run_backfill
from .bridge import bridge_downloads
from .bulk_sync import sync_sources
from .broken import clean_broken
from .config import Config
from .db import connect, init_db
from .evict import evict_completed
from .health import apply_health, pending_releases, summary as health_summary
from .health_worker import run as health_run
from .importer import import_legacy, norm_title
from .intake import set_follow
from .jackett import JackettClient, JackettError
from .jellyfin_leases import touch_active_leases
from .library_sync import sync_library
from .metadata import backfill as metadata_backfill
from .monitoring import monitor_all
from .admin import monitor_titles
from .mover import move_completed
from .normalize_series import normalize_series_titles
from .packs import prune_misassigned_packs
from .policy import (
    TrackerPolicy,
    list_policies,
    reconcile_privacy,
    upsert_policy,
)
from .prune import prune_library
from .public_sync import sync_assets as public_sync
from .prowlarr import ProwlarrClient, ProwlarrError
from .repair import repair_torrents
from .prowlarr_sync import sync_private
from .qbittorrent import QbittorrentClient, QbittorrentError
from .qbit_sync import sync as qbit_sync
from .qbit_limits import (
    DEFAULT_ONDEMAND_KBPS,
    DEFAULT_RADARR_KBPS,
    DEFAULT_SONARR_KBPS,
    apply_limits,
    limits_from_kbps,
)
from .qbit_write import QbittorrentWriteClient
from .resolver import Resolver
from .reseed import reseed_pending
from .retention import evictable, finish_intent, status as seed_status
from .rss_sync import publish_keys, sync_rss
from .runtime_sync import sync_runtimes
from .seeding import normalize_completed
from .strm import export_strm
from .stream_leases import cleanup as cleanup_leases
from .title_sync import upsert_episodes, upsert_movies
from .tmdb import TmdbClient, TmdbError, episode_items, movie_item, tv_item
from .tracker_block import set_tracker_blocked
from .trackers import GENERAL_TRACKERS
from .worker import run_forever


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _record_job(config: Config, kind: str, params: dict, fn):
    """Registra las tareas lanzadas por systemd en el historial del panel."""
    from . import jobs

    try:
        job_id = jobs.create_job(
            config.db_path, kind, params, requested_by="systemd"
        )
    except jobs.JobConflict as exc:
        _print({"job": kind, "skipped": str(exc)})
        return None, None
    jobs.update_job(
        config.db_path, job_id, state="running", started=True, message="iniciada"
    )
    try:
        result = fn()
    except Exception as exc:
        jobs.update_job(
            config.db_path,
            job_id,
            state="error",
            message="error",
            error=str(exc)[:4000],
            finished=True,
        )
        raise
    jobs.update_job(
        config.db_path,
        job_id,
        state="ok",
        progress=1.0,
        message="terminada",
        result=result,
        finished=True,
    )
    return job_id, result


def cmd_init_db(args, config: Config) -> int:
    versions = init_db(config.db_path, config.migrations_dir)
    _print({"db": str(config.db_path), "migrations_applied": versions})
    return 0


def cmd_import_legacy(args, config: Config) -> int:
    source = Path(args.source) if args.source else config.legacy_db
    dest = Path(args.dest) if args.dest else config.db_path
    report = import_legacy(source, dest, config.migrations_dir)
    _print({"source": str(source), "dest": str(dest), **report.as_dict()})
    return 0


def cmd_stats(args, config: Config) -> int:
    resolver = Resolver(config.db_path)
    _print({"db": str(config.db_path), **resolver.stats()})
    return 0


def cmd_resolve(args, config: Config) -> int:
    resolver = Resolver(config.db_path)
    resolution = resolver.resolve(args.key)
    _print(resolution.public())
    return 0 if resolution.source != "unavailable" else 2


def cmd_set_target(args, config: Config) -> int:
    resolver = Resolver(config.db_path)
    result = resolver.set_target(
        args.key,
        args.source,
        release_id=args.release_id,
        file_id=args.file_id,
        note=args.note,
    )
    _print(result)
    return 0


def cmd_targets(args, config: Config) -> int:
    resolver = Resolver(config.db_path)
    _print(resolver.targets(limit=args.limit))
    return 0


def cmd_export_strm(args, config: Config) -> int:
    radarr, sonarr = _arr_clients(config)
    result = export_strm(
        config.db_path,
        args.out,
        base_url=args.base_url,
        limit=args.limit or None,
        key=args.key,
        require_health=args.require_health,
        min_seeders=args.min_seeders,
        versions_filter=args.versions,
        prune=args.prune,
        existing_only=args.existing,
        year=args.year,
        kind=args.kind,
        skip_library=args.skip_library,
        radarr=radarr,
        sonarr=sonarr,
    )
    _print(result)
    return 0


def _pick_series(candidates: list[dict], query: str, year: int | None):
    if not candidates:
        return None
    wanted = norm_title(query)

    def score(item: dict) -> float:
        name = norm_title(item.get("title"))
        value = 0.0
        if name == wanted:
            value += 10
        elif wanted and (wanted in name or name in wanted):
            value += 5
        item_year = item.get("year")
        if year and item_year == year:
            value += 25
        elif year and item_year:
            value -= min(abs(int(item_year) - int(year)), 10)
        value += min(float(item.get("popularity") or 0), 2) / 1000
        return value

    return max(candidates, key=score)


def cmd_tmdb_sync(args, config: Config) -> int:
    client = TmdbClient.from_config(config)
    if args.tv_id or args.tv:
        series = None
        if args.tv_id:
            series = tv_item(client.tv_details(args.tv_id))
        else:
            payload = client.search_tv(
                args.tv, year=args.tv_year, page=1
            )
            candidates = [
                item for item in (
                    tv_item(row) for row in payload.get("results") or []
                ) if item
            ]
            series = _pick_series(candidates, args.tv, args.tv_year)
        if series is None:
            _print({"error": "serie no encontrada", "query": args.tv})
            return 2
        seasons = [
            int(part.strip())
            for part in (args.seasons or "1").split(",")
            if part.strip()
        ]
        episodes: list[dict] = []
        for season in seasons:
            payload = client.season_details(series["tmdb"], season)
            episodes.extend(episode_items(season, payload))
            if args.delay:
                time.sleep(args.delay)
        if args.dry_run:
            _print(
                {
                    "series": series,
                    "seasons": seasons,
                    "episodes": episodes,
                    "dry_run": True,
                }
            )
            return 0
        report = upsert_episodes(config.db_path, series, episodes)
        keys = [
            f"series:tmdb:{int(series['tmdb'])}:{int(item['season'])}:{int(item['episode'])}"
            for item in episodes
        ]
        followed = set_follow(config.db_path, keys, follow=not args.no_follow)
        _print(
            {
                "series": series,
                "seasons": seasons,
                "episodes": len(episodes),
                "followed": followed,
                **report,
            }
        )
        return 0

    items: list[dict] = []
    page = args.page
    pages = 0
    while len(items) < args.limit:
        if args.year:
            payload = client.discover_movies(
                year=args.year,
                min_votes=args.min_votes,
                sort_by=args.sort,
                page=page,
            )
        else:
            payload = client.popular_movies(page=page)
        results = payload.get("results") or []
        if not results:
            break
        for result in results:
            item = movie_item(result)
            if item:
                items.append(item)
        pages += 1
        total_pages = int(payload.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
        if args.delay:
            time.sleep(args.delay)
    items = items[: args.limit]
    if args.dry_run:
        _print(
            {
                "year": args.year,
                "count": len(items),
                "pages": pages,
                "items": items,
                "dry_run": True,
            }
        )
        return 0
    report = upsert_movies(config.db_path, items, source="tmdb")
    keys = [f"movie:tmdb:{int(item['tmdb'])}" for item in items if item.get("tmdb")]
    followed = set_follow(config.db_path, keys, follow=not args.no_follow)
    _print({
        "year": args.year,
        "count": len(items),
        "pages": pages,
        "followed": followed,
        **report,
    })
    return 0


def cmd_normalize_series(args, config: Config) -> int:
    client = TmdbClient.from_config(config)
    report = normalize_series_titles(
        config.db_path,
        client,
        delay=args.delay,
        dry_run=args.dry_run,
        limit=args.limit or None,
    )
    _print(report)
    return 0


def cmd_metadata(args, config: Config) -> int:
    client = TmdbClient.from_config(config)
    report = metadata_backfill(
        config.db_path,
        client,
        limit=args.limit,
        delay=args.delay,
        workers=args.workers,
        missing_only=not args.force,
    )
    _print(report)
    return 0


def cmd_monitor(args, config: Config) -> int:
    _, report = _record_job(
        config,
        "monitor",
        {"limit": args.limit, "delay": args.delay, "scheduled": True},
        lambda: monitor_titles(
            config.db_path,
            config,
            limit=args.limit,
            delay=args.delay,
        ),
    )
    if report is None:
        return 0
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_bulk_sync(args, config: Config) -> int:
    client = ProwlarrClient.from_config(config)
    jackett = None
    jackett_error = None
    if not args.no_jackett:
        try:
            jackett = JackettClient.from_config(config)
        except JackettError as exc:
            jackett_error = str(exc)
    trackers = (
        tuple(part.strip() for part in args.trackers.split(",") if part.strip())
        if args.trackers
        else GENERAL_TRACKERS
    )
    report = sync_sources(
        config.db_path,
        client,
        jackett=jackett,
        year=args.year,
        limit=args.limit,
        delay=args.delay,
        trackers=trackers,
        min_seeders=args.min_seeders,
        key=args.key,
        kind=args.kind,
        dry_run=args.dry_run,
        workers=max(1, args.workers),
        id_fallback=True,
    )
    if jackett_error:
        report["jackett_error"] = jackett_error
    _print({"year": args.year, **report})
    return 0


def cmd_set_policy(args, config: Config) -> int:
    policy = TrackerPolicy(
        tracker=args.tracker,
        min_seed_hours=args.min_seed_hours,
        target_ratio=args.target_ratio,
        max_active=args.max_active,
        allow_partial=args.allow_partial,
        keep_for_bonus=args.keep_for_bonus,
        privacy=args.privacy,
        enabled=args.enabled,
        abandon_under_percent=args.abandon_under_percent,
        abandon_action=args.abandon_action,
        notes=args.notes,
    )
    upsert_policy(str(config.db_path), policy)
    # Corregir la privacidad también corrige releases, política y
    # compromisos abiertos: un tracker público no exige siembra.
    with connect(config.db_path) as conn:
        reconcile_privacy(conn, policy.tracker, policy.privacy)
        conn.commit()
    _print(
        {
            "tracker": policy.tracker,
            "min_seed_hours": policy.min_seed_hours,
            "target_ratio": policy.target_ratio,
            "max_active": policy.max_active,
            "allow_partial": policy.allow_partial,
            "keep_for_bonus": policy.keep_for_bonus,
            "privacy": policy.privacy,
            "enabled": policy.enabled,
            "abandon_under_percent": policy.abandon_under_percent,
            "abandon_action": policy.abandon_action,
            "notes": policy.notes,
        }
    )
    return 0


def cmd_policies(args, config: Config) -> int:
    _print(list_policies(str(config.db_path)))
    return 0


def cmd_admit(args, config: Config) -> int:
    data_path = args.data_path or str(config.seed_root)
    result = admit(
        config.db_path,
        args.key,
        release_id=args.release_id,
        data_path=data_path,
        reserve_bytes=int(args.reserve_gb * 1024**3),
        require_health=args.require_health,
        min_seeders=args.min_seeders,
        max_size_bytes=config.max_stream_bytes,
        persist=not args.dry_run,
    )
    _print(result.public())
    return 0 if result.decision.allowed else 2


def cmd_intents(args, config: Config) -> int:
    _print(list_intents(config.db_path, limit=args.limit))
    return 0


def cmd_cancel_intent(args, config: Config) -> int:
    _print(cancel_intent(config.db_path, args.intent_id, args.reason))
    return 0


def cmd_can_finish(args, config: Config) -> int:
    current = seed_status(config.db_path, args.intent_id)
    if current is None:
        _print({"error": "intent no encontrado", "intent_id": args.intent_id})
        return 2
    _print(
        {
            "intent_id": current.intent_id,
            "key": current.key,
            "tracker": current.tracker,
            "state": current.state,
            "seed_started_at": current.seed_started_at,
            "ratio": current.ratio,
            "allowed": current.allowed,
            "reason": current.reason,
        }
    )
    return 0 if current.allowed else 2


def cmd_finish_intent(args, config: Config) -> int:
    result = finish_intent(config.db_path, args.intent_id)
    _print(result)
    return 0 if result["closed"] else 2


def cmd_evictable(args, config: Config) -> int:
    _print(evictable(config.db_path))
    return 0


def cmd_set_health(args, config: Config) -> int:
    result = apply_health(
        str(config.db_path),
        args.release_id,
        seeders=args.seeders,
        peers=args.peers,
        checked_at=args.checked_at,
        health=args.health,
    )
    _print(result)
    return 0 if result else 2


def cmd_health(args, config: Config) -> int:
    _print(health_summary(str(config.db_path)))
    return 0


def cmd_pending_health(args, config: Config) -> int:
    _print(pending_releases(str(config.db_path), limit=args.limit))
    return 0


def cmd_health_check(args, config: Config) -> int:
    try:
        client = ProwlarrClient.from_config(config)
    except ProwlarrError as exc:
        _print({"error": str(exc)})
        return 2
    report = health_run(
        str(config.db_path),
        client,
        limit=args.limit,
        delay=args.delay,
        dry_run=args.dry_run,
        tracker=args.tracker,
        key=args.key,
        ttl_hours=args.ttl_hours,
    )
    _print(report)
    return 0 if report["errors"] == 0 or report["checked"] > 0 else 3


def _qbit(config: Config) -> QbittorrentClient | None:
    try:
        return QbittorrentClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return None


def _arr_clients(config: Config):
    radarr = (
        RadarrClient.from_config(config) if config.radarr_api_key else None
    )
    sonarr = (
        SonarrClient.from_config(config) if config.sonarr_api_key else None
    )
    return radarr, sonarr


def cmd_qbit_version(args, config: Config) -> int:
    client = _qbit(config)
    if client is None:
        return 2
    _print({"version": client.app_version()})
    return 0


def cmd_qbit_torrents(args, config: Config) -> int:
    client = _qbit(config)
    if client is None:
        return 2
    torrents = client.torrents(category=args.category)
    _print(torrents)
    return 0


def cmd_qbit_files(args, config: Config) -> int:
    client = _qbit(config)
    if client is None:
        return 2
    _print(client.files(args.hash))
    return 0


def cmd_qbit_properties(args, config: Config) -> int:
    client = _qbit(config)
    if client is None:
        return 2
    _print(client.properties(args.hash))
    return 0


def cmd_qbit_sync(args, config: Config) -> int:
    client = _qbit(config)
    if client is None:
        return 2
    radarr, sonarr = _arr_clients(config)
    report = qbit_sync(
        config.db_path,
        client,
        radarr=radarr,
        sonarr=sonarr,
        archive_root=config.archive_roots(),
        dry_run=args.dry_run,
        check_exists=not args.no_check_paths,
    )
    _print(report.public())
    return 0 if not report.errors else 1


def cmd_qbit_limits(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    limits = limits_from_kbps(
        args.radarr_kbps, args.sonarr_kbps, args.ondemand_kbps
    )
    report = apply_limits(client, limits, dry_run=args.dry_run)
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_move_completed(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = move_completed(
        str(config.db_path),
        client,
        target=args.target or str(config.library_root / "descargas"),
        source_root=str(config.ondemand_save_path),
        grace_seconds=args.grace,
        limit=args.limit,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_runtime_sync(args, config: Config) -> int:
    client = TmdbClient.from_config(config)
    report = sync_runtimes(
        str(config.db_path),
        client,
        limit=args.limit,
        delay=args.delay,
        only_missing=not args.all,
        kind=args.kind,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_prowlarr_sync(args, config: Config) -> int:
    try:
        client = ProwlarrClient.from_config(config)
    except ProwlarrError as exc:
        _print({"error": str(exc)})
        return 2
    report = sync_private(
        str(config.db_path),
        client,
        tracker=args.tracker,
        indexer_id=args.indexer_id,
        key=args.key,
        limit=args.limit,
        delay=args.delay,
        min_seeders=args.min_seeders,
        min_year=args.min_year,
        include_existing=args.include_existing,
        dry_run=args.dry_run,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_public_sync(args, config: Config) -> int:
    try:
        client = JackettClient.from_config(config)
    except JackettError as exc:
        _print({"error": str(exc)})
        return 2
    indexers = [item.strip() for item in args.indexers.split(",") if item.strip()]
    report = public_sync(
        str(config.db_path),
        client,
        key=args.key,
        limit=args.limit,
        indexers=indexers or None,
        dry_run=args.dry_run,
        delay=args.delay,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_abandon(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = abandon_idle(
        str(config.db_path),
        client,
        limit=args.limit,
        grace_seconds=args.grace,
        min_age_seconds=args.min_age,
        allow_private_delete=args.private_delete,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_backfill(args, config: Config) -> int:
    try:
        prowlarr = ProwlarrClient.from_config(config)
    except ProwlarrError as exc:
        _print({"error": str(exc)})
        return 2
    jackett = None
    try:
        jackett = JackettClient.from_config(config)
    except JackettError:
        pass
    years = [
        int(part.strip())
        for part in args.years.split(",")
        if part.strip()
    ]
    report = run_backfill(
        str(config.db_path),
        prowlarr,
        jackett,
        years=years,
        limit=args.limit,
        delay=args.delay,
        out_dir=config.catalog_library,
        base_url=args.base_url,
        dry_run=args.dry_run,
        skip_export=args.no_export,
    )
    _print(report)
    return 0


def cmd_rss_sync(args, config: Config) -> int:
    try:
        prowlarr = ProwlarrClient.from_config(config)
    except ProwlarrError as exc:
        _print({"error": str(exc)})
        return 2
    jackett = None
    try:
        jackett = JackettClient.from_config(config)
    except JackettError:
        pass
    tmdb = TmdbClient.from_config(config)

    def run():
        report = sync_rss(
            str(config.db_path),
            prowlarr,
            jackett,
            tmdb,
            limit=args.limit,
            delay=args.delay,
            dry_run=args.dry_run,
        )
        keys = report.pop("keys", [])
        publish = publish_keys(
            config.db_path,
            config.catalog_library,
            keys[: args.export_limit or len(keys)],
            base_url=args.base_url,
            dry_run=args.dry_run,
        )
        report["exported_keys"] = publish["exported"]
        report["unmonitored_keys"] = publish["unmonitored"]
        return report

    _, report = _record_job(
        config,
        "rss-sync",
        {
            "limit": args.limit,
            "delay": args.delay,
            "dry_run": args.dry_run,
            "scheduled": True,
        },
        run,
    )
    if report is None:
        return 0
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_bridge(args, config: Config) -> int:
    try:
        radarr = RadarrClient.from_config(config)
        sonarr = SonarrClient.from_config(config)
    except ArrError as exc:
        _print({"error": str(exc)})
        return 2
    report = bridge_downloads(
        str(config.db_path),
        radarr,
        sonarr,
        limit=args.limit,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_sync_library(args, config: Config) -> int:
    radarr, sonarr = _arr_clients(config)
    report = sync_library(
        str(config.db_path),
        radarr=radarr,
        sonarr=sonarr,
    )
    _print(report)
    return 0 if report.get("ok") else 2


def cmd_restore_strm(args, config: Config) -> int:
    years = [
        int(part.strip())
        for part in args.years.split(",")
        if part.strip()
    ]
    out = args.out or str(config.catalog_library)
    radarr, sonarr = _arr_clients(config)
    report: dict = {}
    for year in years:
        report[f"movie_{year}"] = export_strm(
            config.db_path,
            out,
            base_url=args.base_url,
            limit=None,
            year=year,
            kind="movie",
            skip_library=False,
            radarr=radarr,
            sonarr=sonarr,
        )
    if not args.no_series:
        report["series"] = export_strm(
            config.db_path,
            out,
            base_url=args.base_url,
            limit=None,
            kind="series",
            skip_library=False,
            radarr=radarr,
            sonarr=sonarr,
        )
    _print(report)
    return 0


def cmd_prune_library(args, config: Config) -> int:
    radarr, sonarr = _arr_clients(config)
    report = prune_library(
        str(config.db_path),
        args.out,
        radarr=radarr,
        sonarr=sonarr,
        dry_run=not args.execute,
    )
    _print(report)
    return 0


def cmd_prune_packs(args, config: Config) -> int:
    report = prune_misassigned_packs(
        str(config.db_path), dry_run=not args.execute
    )
    _print(report)
    return 0


def cmd_monitor_all(args, config: Config) -> int:
    report = monitor_all(
        str(config.db_path),
        dry_run=not args.execute,
        include_arr=not args.exclude_arr,
    )
    _print(report)
    return 0


def cmd_jellyfin_leases(args, config: Config) -> int:
    if not config.jellyfin_api_key:
        _print({"error": "sin JELLYFIN_API_KEY"})
        return 2
    try:
        report = touch_active_leases(
            str(config.db_path),
            base_url=args.url or config.jellyfin_url,
            api_key=config.jellyfin_api_key,
        )
    except Exception as exc:
        _print({"error": str(exc)[:200]})
        return 1
    _print(report)
    return 0


def cmd_block_tracker(args, config: Config) -> int:
    report = set_tracker_blocked(
        str(config.db_path),
        args.tracker,
        blocked=not args.unblock,
        reason=args.reason,
        dry_run=not args.execute,
    )
    _print(report)
    return 0


def cmd_clean_broken(args, config: Config) -> int:
    try:
        qbit_read = QbittorrentClient.from_config(config)
        qbit_write = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = clean_broken(
        str(config.db_path),
        qbit_read,
        qbit_write,
        library_root=args.library or config.catalog_library,
        limit=args.limit,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_repair(args, config: Config) -> int:
    try:
        qbit_read = QbittorrentClient.from_config(config)
        qbit_write = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = repair_torrents(
        str(config.db_path), qbit_read, qbit_write,
        limit=args.limit, dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_reseed(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = reseed_pending(
        str(config.db_path),
        client,
        limit=args.limit,
        jackett_api_key=config.jackett_api_key,
        save_path=str(config.ondemand_save_path),
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_evict(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = evict_completed(
        str(config.db_path),
        client,
        limit=args.limit,
        grace_seconds=args.grace,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_normalize_seeding(args, config: Config) -> int:
    try:
        client = QbittorrentWriteClient.from_config(config)
    except QbittorrentError as exc:
        _print({"error": str(exc)})
        return 2
    report = normalize_completed(
        str(config.db_path),
        client,
        limit=args.limit,
        dry_run=not args.execute,
    )
    _print(report)
    return 0 if not report["errors"] else 1


def cmd_worker(args, config: Config) -> int:
    qbit_read = None if args.no_sync else _qbit(config)
    qbit_write = None
    if args.execute_moves:
        try:
            qbit_write = QbittorrentWriteClient.from_config(config)
        except QbittorrentError as exc:
            _print({"error": f"no se puede mover: {exc}"})
            return 2
    else:
        try:
            qbit_write = QbittorrentWriteClient.from_config(config)
        except QbittorrentError:
            qbit_write = None

    arr_radarr, arr_sonarr = _arr_clients(config)

    def cleanup() -> dict:
        return {"leases": cleanup_leases(str(config.db_path), before_seconds=3600)}

    def sync() -> dict:
        if qbit_read is None:
            return {"skipped": "no_sync"}
        return qbit_sync(
            str(config.db_path), qbit_read,
            radarr=arr_radarr, sonarr=arr_sonarr,
            archive_root=config.archive_roots(),
            check_exists=True,
        ).public()

    def reseed() -> dict:
        if qbit_write is None:
            return {"skipped": "sin_qbit"}
        return reseed_pending(
            str(config.db_path), qbit_write,
            limit=args.reseed_limit,
            jackett_api_key=config.jackett_api_key,
            save_path=str(config.ondemand_save_path),
            dry_run=not args.execute_reseed,
        )

    def clean() -> dict:
        if qbit_read is None or qbit_write is None:
            return {"skipped": "sin_qbit"}
        return clean_broken(
            str(config.db_path), qbit_read, qbit_write,
            library_root=config.catalog_library,
            limit=args.clean_limit,
            dry_run=not args.execute_clean,
        )

    def library() -> dict:
        return sync_library(
            str(config.db_path),
            radarr=arr_radarr,
            sonarr=arr_sonarr,
        )

    def repair() -> dict:
        if qbit_read is None or qbit_write is None:
            return {"skipped": "sin_qbit"}
        return repair_torrents(
            str(config.db_path), qbit_read, qbit_write,
            limit=args.repair_limit,
            dry_run=not args.execute_repair,
        )

    def moves() -> dict:
        if qbit_write is None:
            return {"skipped": "sin_qbit"}
        return move_completed(
            str(config.db_path), qbit_write,
            source_root=str(config.ondemand_save_path),
            grace_seconds=args.grace,
            limit=args.move_limit,
            dry_run=not args.execute_moves,
        )

    def abandon() -> dict:
        if qbit_write is None:
            return {"skipped": "sin_qbit"}
        return abandon_idle(
            str(config.db_path), qbit_write,
            limit=args.abandon_limit,
            grace_seconds=args.grace,
            min_age_seconds=args.abandon_min_age,
            allow_private_delete=args.abandon_private_delete,
            dry_run=not args.execute_abandon,
        )

    def seeding() -> dict:
        if qbit_write is None:
            return {"skipped": "sin_qbit"}
        return normalize_completed(
            str(config.db_path), qbit_write,
            limit=args.seeding_limit,
            dry_run=not args.execute_seeding,
        )

    def evict() -> dict:
        if qbit_write is None:
            return {"skipped": "sin_qbit"}
        return evict_completed(
            str(config.db_path), qbit_write,
            limit=args.evict_limit,
            grace_seconds=args.grace,
            dry_run=not args.execute_evict,
        )

    run_forever(
        cleanup=cleanup,
        sync=sync if qbit_read is not None else None,
        library=library,
        clean=clean if (
            qbit_read is not None
            and qbit_write is not None
            and args.execute_clean
        ) else None,
        repair=repair if (
            qbit_read is not None
            and qbit_write is not None
            and args.execute_repair
        ) else None,
        reseed=reseed if (qbit_write is not None and args.execute_reseed) else None,
        abandon=abandon if (qbit_write is not None and not args.no_abandon) else None,
        moves=moves if qbit_write is not None else None,
        seeding=seeding if (qbit_write is not None and not args.no_seeding) else None,
        evict=evict if (qbit_write is not None and args.execute_evict) else None,
        interval=args.interval,
        once=args.once,
        emit=lambda obj: _print(obj),
    )
    return 0


def cmd_serve(args, config: Config) -> int:
    """Servicio único: web + API + motor de reproducción + scheduler."""
    from .web import run

    return run(args.host, args.port, config)


def cmd_service(args, config: Config) -> int:
    return cmd_serve(args, config)


def cmd_web(args, config: Config) -> int:
    return cmd_serve(args, config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tod")
    parser.add_argument(
        "--db", help="ruta de la base (por defecto TOD_DB o var/tod.db)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="crea/migra la base nueva")

    p = sub.add_parser("import-legacy", help="importa assets.db sin destruirla")
    p.add_argument("--source", help="assets.db de origen")
    p.add_argument("--dest", help="base destino")

    sub.add_parser("stats", help="cuenta filas por tabla")

    p = sub.add_parser("resolve", help="resuelve una clave estable")
    p.add_argument("key")

    p = sub.add_parser("set-target", help="cambia el puntero mutable")
    p.add_argument("key")
    p.add_argument(
        "--source",
        required=True,
        choices=["auto", "library", "local_seed", "torrent", "unavailable"],
    )
    p.add_argument("--release-id", type=int)
    p.add_argument("--file-id", type=int)
    p.add_argument("--note")

    p = sub.add_parser("targets", help="lista punteros")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("export-strm", help="genera .strm de previsualización")
    p.add_argument("--out", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8773")
    p.add_argument("--key")
    p.add_argument("--year", type=int)
    p.add_argument("--kind", choices=["movie", "series"])
    p.add_argument("--skip-library", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--require-health", action="store_true")
    p.add_argument("--min-seeders", type=int, default=1)
    p.add_argument(
        "--versions",
        choices=["available", "alive", "all"],
        default="available",
        help="qué versiones reciben .strm propio (available: solo reproducibles)",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="borra .strm obsoletos en una exportación completa",
    )
    p.add_argument(
        "--existing",
        action="store_true",
        help="solo títulos que ya tienen .strm en la salida",
    )

    p = sub.add_parser("tmdb-sync", help="alta masiva de títulos desde TMDB")
    p.add_argument("--year", type=int)
    p.add_argument("--min-votes", type=int, default=50)
    p.add_argument("--sort", default="popularity.desc")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--delay", type=float, default=0.25)
    p.add_argument("--tv-id", type=int)
    p.add_argument("--tv")
    p.add_argument("--tv-year", type=int)
    p.add_argument("--seasons", default="1")
    p.add_argument("--no-follow", action="store_true", help="añade sin seguimiento")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("normalize-series", help="normaliza títulos de series desde TMDB")
    p.add_argument("--delay", type=float, default=0.3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("metadata", help="rellena póster, sinopsis y nota desde TMDB")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--delay", type=float, default=0.08)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--force", action="store_true", help="revisa también los ya rellenados")

    p = sub.add_parser("monitor", help="busca fuentes de títulos en seguimiento")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--delay", type=float, default=2.0)

    p = sub.add_parser("bulk-sync", help="busca releases de los títulos en Prowlarr")
    p.add_argument("--year", type=int)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--delay", type=float, default=8.0)
    p.add_argument("--min-seeders", type=int, default=1)
    p.add_argument("--kind", choices=["movie", "series"], default="movie")
    p.add_argument("--trackers", help="nombres separados por coma")
    p.add_argument("--no-jackett", action="store_true")
    p.add_argument("--key")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--workers", type=int, default=4,
        help="búsquedas en paralelo (1 = secuencial)",
    )

    p = sub.add_parser(
        "service",
        help="servicio único: web, API, reproducción y scheduler",
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8773)

    p = sub.add_parser("serve", help="alias de service")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8773)

    p = sub.add_parser("web", help="alias de service (web unificada)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8773)

    p = sub.add_parser("set-policy", help="fija la política de seed de un tracker")
    p.add_argument("tracker")
    p.add_argument("--min-seed-hours", type=int, default=0)
    p.add_argument("--target-ratio", type=float, default=0.0)
    p.add_argument("--max-active", type=int)
    p.add_argument(
        "--allow-partial",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    p.add_argument(
        "--keep-for-bonus",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    p.add_argument(
        "--privacy",
        choices=["private", "public"],
        default="private",
    )
    p.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    p.add_argument("--abandon-under-percent", type=int, default=0)
    p.add_argument(
        "--abandon-action",
        choices=["keep", "pause", "delete"],
        default="keep",
    )
    p.add_argument("--notes")

    sub.add_parser("policies", help="lista políticas de seed")

    p = sub.add_parser("admit", help="comprueba y registra el compromiso antes de descargar")
    p.add_argument("key")
    p.add_argument("--release-id", type=int)
    p.add_argument("--data-path")
    p.add_argument("--reserve-gb", type=float, default=20.0)
    p.add_argument("--min-seeders", type=int, default=1)
    p.add_argument(
        "--require-health",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("intents", help="lista compromisos de descarga/seed")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("cancel-intent", help="cancela un compromiso")
    p.add_argument("intent_id", type=int)
    p.add_argument("--reason", default="cancelado")

    p = sub.add_parser("can-finish", help="comprueba si ya se puede cerrar/borrar")
    p.add_argument("intent_id", type=int)

    p = sub.add_parser("finish-intent", help="cierra un compromiso si ya cumplió")
    p.add_argument("intent_id", type=int)

    sub.add_parser("evictable", help="lista compromisos que ya se pueden cerrar")

    p = sub.add_parser("set-health", help="actualiza salud/peers de un release")
    p.add_argument("--release-id", type=int, required=True)
    p.add_argument("--seeders", type=int, default=0)
    p.add_argument("--peers", type=int, default=0)
    p.add_argument(
        "--health",
        choices=["unknown", "alive", "stale", "dead", "blocked"],
        help="si se omite, se clasifica con seeders/peers/fecha",
    )
    p.add_argument("--checked-at", help="ISO 8601; por defecto ahora")

    sub.add_parser("health", help="resumen de salud")

    p = sub.add_parser("pending-health", help="releases pendientes de verificar")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("health-check", help="consulta Prowlarr en solo lectura")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--tracker")
    p.add_argument("--key")
    p.add_argument("--ttl-hours", type=int, default=24)

    sub.add_parser("qbit-version", help="versión de qBittorrent (solo lectura)")
    p = sub.add_parser("qbit-torrents", help="lista torrents (solo lectura)")
    p.add_argument("--category")
    p = sub.add_parser("qbit-files", help="ficheros de un torrent (solo lectura)")
    p.add_argument("hash")
    p = sub.add_parser("qbit-properties", help="propiedades de un torrent (solo lectura)")
    p.add_argument("hash")

    p = sub.add_parser("qbit-sync", help="sincroniza qBittorrent en solo lectura")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-check-paths", action="store_true")

    p = sub.add_parser(
        "qbit-limits",
        help="aplica límites de descarga por categoría (Radarr/Sonarr)",
    )
    p.add_argument("--radarr-kbps", type=int, default=DEFAULT_RADARR_KBPS)
    p.add_argument("--sonarr-kbps", type=int, default=DEFAULT_SONARR_KBPS)
    p.add_argument("--ondemand-kbps", type=int, default=DEFAULT_ONDEMAND_KBPS)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("worker", help="ciclo automático de sync/abandon/move/seeding")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=300.0)
    p.add_argument("--execute-moves", action="store_true")
    p.add_argument("--execute-abandon", action="store_true")
    p.add_argument("--execute-seeding", action="store_true")
    p.add_argument("--execute-evict", action="store_true")
    p.add_argument("--execute-reseed", action="store_true")
    p.add_argument("--execute-clean", action="store_true")
    p.add_argument("--execute-repair", action="store_true")
    p.add_argument("--move-limit", type=int, default=10)
    p.add_argument("--abandon-limit", type=int, default=20)
    p.add_argument("--abandon-min-age", type=int, default=600)
    p.add_argument("--abandon-private-delete", action="store_true")
    p.add_argument("--seeding-limit", type=int, default=50)
    p.add_argument("--evict-limit", type=int, default=10)
    p.add_argument("--reseed-limit", type=int, default=1)
    p.add_argument("--clean-limit", type=int, default=10)
    p.add_argument("--repair-limit", type=int, default=5)
    p.add_argument("--grace", type=int, default=300)
    p.add_argument("--no-sync", action="store_true")
    p.add_argument("--no-abandon", action="store_true")
    p.add_argument("--no-seeding", action="store_true")
    p.add_argument("--no-evict", action="store_true")

    p = sub.add_parser("repair", help="recheck/reanuda torrents con error local")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser(
        "jellyfin-leases",
        help="renueva leases de lo que se está viendo en Jellyfin",
    )
    p.add_argument("--url")

    p = sub.add_parser(
        "block-tracker",
        help="bloquea un tracker para descargas (marca releases y desactiva política)",
    )
    p.add_argument("tracker")
    p.add_argument("--unblock", action="store_true")
    p.add_argument("--reason", default="downloads_broken")
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser("prune-library", help="borra .strm de títulos ya en Radarr/Sonarr")
    p.add_argument("--out", required=True)
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser(
        "prune-packs",
        help="borra packs asignados a episodios de otra temporada",
    )
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser(
        "monitor-all",
        help="marca todas las fichas para seguimiento (una sola vez)",
    )
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--exclude-arr", action="store_true",
        help="no marca lo que ya está en Radarr/Sonarr",
    )

    p = sub.add_parser("bridge", help="copia al *arr lo descargado por el catálogo")
    p.add_argument(
        "--limit", type=int, default=1,
        help="descargas por ejecución; un pack cuenta como una",
    )
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser("rss-sync", help="ingesta novedades por RSS/latest")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--delay", type=float, default=0.3)
    p.add_argument("--base-url", default="http://127.0.0.1:8773")
    p.add_argument("--export-limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("backfill", help="lote de fondo antiguo por años")
    p.add_argument(
        "--years", default="2019,2018,2017,2016,2015,2014,2013,2012,2011,2010"
    )
    p.add_argument("--limit", type=int, default=250)
    p.add_argument("--delay", type=float, default=8.0)
    p.add_argument("--base-url", default="http://127.0.0.1:8773")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-export", action="store_true")

    p = sub.add_parser("sync-library", help="sincroniza library_files desde Radarr/Sonarr")

    p = sub.add_parser(
        "restore-strm",
        help="repone .strm de contenido que ya no está en la biblioteca",
    )
    p.add_argument(
        "--years",
        default=",".join(
            str(year)
            for year in range(datetime.now().year, datetime.now().year - 7, -1)
        ),
    )
    p.add_argument("--out")
    p.add_argument("--base-url", default="http://127.0.0.1:8773")
    p.add_argument("--no-series", action="store_true")

    p = sub.add_parser(
        "normalize-seeding",
        help="quita forceStart a torrents completos para que siembren normal",
    )
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser(
        "runtime-sync", help="rellena duración de películas desde TMDB"
    )
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--delay", type=float, default=0.25)
    p.add_argument("--kind", choices=["movie", "series"], default="movie")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("prowlarr-sync", help="ingesta candidatos privados de Prowlarr")
    p.add_argument("--tracker", required=True)
    p.add_argument("--indexer-id", type=int, required=True)
    p.add_argument("--key")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--min-seeders", type=int, default=1)
    p.add_argument("--min-year", type=int, default=0)
    p.add_argument("--include-existing", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("public-sync", help="fallback público vía Jackett")
    p.add_argument("--key")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--indexers", default="")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load(Path(args.db) if args.db else None)
    config.ensure_dirs()
    handlers = {
        "init-db": cmd_init_db,
        "import-legacy": cmd_import_legacy,
        "stats": cmd_stats,
        "resolve": cmd_resolve,
        "set-target": cmd_set_target,
        "targets": cmd_targets,
        "export-strm": cmd_export_strm,
        "tmdb-sync": cmd_tmdb_sync,
        "normalize-series": cmd_normalize_series,
        "metadata": cmd_metadata,
        "monitor": cmd_monitor,
        "bulk-sync": cmd_bulk_sync,
        "set-policy": cmd_set_policy,
        "policies": cmd_policies,
        "admit": cmd_admit,
        "intents": cmd_intents,
        "cancel-intent": cmd_cancel_intent,
        "can-finish": cmd_can_finish,
        "finish-intent": cmd_finish_intent,
        "evictable": cmd_evictable,
        "set-health": cmd_set_health,
        "health": cmd_health,
        "pending-health": cmd_pending_health,
        "health-check": cmd_health_check,
        "qbit-version": cmd_qbit_version,
        "qbit-torrents": cmd_qbit_torrents,
        "qbit-files": cmd_qbit_files,
        "qbit-properties": cmd_qbit_properties,
        "qbit-sync": cmd_qbit_sync,
        "qbit-limits": cmd_qbit_limits,
        "prowlarr-sync": cmd_prowlarr_sync,
        "runtime-sync": cmd_runtime_sync,
        "public-sync": cmd_public_sync,
        "worker": cmd_worker,
        "repair": cmd_repair,
        "block-tracker": cmd_block_tracker,
        "jellyfin-leases": cmd_jellyfin_leases,
        "prune-library": cmd_prune_library,
        "prune-packs": cmd_prune_packs,
        "monitor-all": cmd_monitor_all,
        "bridge": cmd_bridge,
        "rss-sync": cmd_rss_sync,
        "backfill": cmd_backfill,
        "sync-library": cmd_sync_library,
        "restore-strm": cmd_restore_strm,
        "normalize-seeding": cmd_normalize_seeding,
        "serve": cmd_serve,
        "service": cmd_service,
        "web": cmd_web,
    }
    return handlers[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
