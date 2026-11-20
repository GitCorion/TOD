-- Cimientos del catálogo.
-- La URL pública (.strm) apunta a assets.key; el puntero real vive aquí y es mutable.

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    legacy_asset_id INTEGER UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('movie', 'series')),
    tmdb INTEGER,
    imdb TEXT,
    season INTEGER,
    episode INTEGER,
    title TEXT,
    year INTEGER,
    identity_status TEXT NOT NULL DEFAULT 'ok'
        CHECK (identity_status IN ('ok', 'needs_identity')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_assets_identity
    ON assets(kind, tmdb, season, episode);
CREATE INDEX IF NOT EXISTS ix_assets_title_year
    ON assets(title, year);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    legacy_release_id INTEGER UNIQUE,
    tracker TEXT NOT NULL,
    guid TEXT UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT,
    year INTEGER,
    quality TEXT,
    codec TEXT,
    audio TEXT,
    size_bytes INTEGER,
    is_pack INTEGER NOT NULL DEFAULT 0,
    info_hash TEXT,
    download_url TEXT,
    seeders INTEGER,
    peers INTEGER,
    health TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health IN ('unknown', 'alive', 'stale', 'dead', 'blocked')),
    health_checked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_releases_asset ON releases(asset_id);
CREATE INDEX IF NOT EXISTS ix_releases_health ON releases(health);
CREATE UNIQUE INDEX IF NOT EXISTS ux_releases_info_hash
    ON releases(info_hash) WHERE info_hash IS NOT NULL AND info_hash <> '';

CREATE TABLE IF NOT EXISTS torrents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    info_hash TEXT NOT NULL UNIQUE,
    release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
    tracker TEXT,
    state TEXT NOT NULL DEFAULT 'waiting'
        CHECK (state IN ('waiting', 'downloading', 'complete', 'seeding',
                         'paused', 'error', 'removed')),
    save_path TEXT,
    temp_path TEXT,
    added_at TEXT,
    completed_at TEXT,
    seed_started_at TEXT,
    seed_required_until TEXT,
    uploaded INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    ratio REAL NOT NULL DEFAULT 0,
    last_sync TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_torrents_release ON torrents(release_id);
CREATE INDEX IF NOT EXISTS ix_torrents_state ON torrents(state);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
    torrent_id INTEGER REFERENCES torrents(id) ON DELETE SET NULL,
    legacy_file_id INTEGER UNIQUE,
    file_index INTEGER,
    rel_path TEXT,
    local_path TEXT,
    library_path TEXT,
    size_bytes INTEGER,
    state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (state IN ('unknown', 'downloading', 'ready', 'seeding',
                         'archived', 'missing', 'evicted')),
    media_info TEXT,
    last_seen TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_files_asset ON files(asset_id);
CREATE INDEX IF NOT EXISTS ix_files_state ON files(state);

-- El puntero mutable. Cambiar de release/fichero/ubicación no toca el .strm.
CREATE TABLE IF NOT EXISTS play_targets (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
    file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
    source_kind TEXT NOT NULL DEFAULT 'auto'
        CHECK (source_kind IN ('auto', 'library', 'local_seed', 'torrent', 'unavailable')),
    note TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS tracker_policies (
    tracker TEXT PRIMARY KEY,
    min_seed_hours INTEGER,
    target_ratio REAL,
    max_active INTEGER,
    allow_partial INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kind TEXT NOT NULL,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS ix_events_asset_ts ON events(asset_id, ts);
