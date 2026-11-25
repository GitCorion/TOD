-- Perfiles de calidad, Custom Formats, restricciones, etiquetas y Root Folders.

CREATE TABLE IF NOT EXISTS quality_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_default INTEGER NOT NULL DEFAULT 0,
    quality_order_json TEXT NOT NULL,
    allowed_qualities_json TEXT NOT NULL,
    cutoff_quality TEXT,
    language_preference TEXT NOT NULL DEFAULT 'inherit',
    upgrade_allowed INTEGER NOT NULL DEFAULT 1,
    prefer_smaller INTEGER NOT NULL DEFAULT 1,
    max_size_gb REAL NOT NULL DEFAULT 0,
    max_mb_per_min REAL NOT NULL DEFAULT 0,
    min_mb_per_min REAL NOT NULL DEFAULT 0,
    block_cam INTEGER NOT NULL DEFAULT 1,
    block_disc_images INTEGER NOT NULL DEFAULT 1,
    min_score INTEGER NOT NULL DEFAULT 0,
    cutoff_score INTEGER,
    radarr_profile_id INTEGER,
    sonarr_profile_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS custom_formats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    include_when_renaming INTEGER NOT NULL DEFAULT 0,
    specs_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS profile_format_scores (
    profile_id INTEGER NOT NULL REFERENCES quality_profiles(id) ON DELETE CASCADE,
    format_id INTEGER NOT NULL REFERENCES custom_formats(id) ON DELETE CASCADE,
    score INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, format_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#6b7280',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS media_tags (
    media_kind TEXT NOT NULL CHECK (media_kind IN ('movie', 'series')),
    tmdb INTEGER NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (media_kind, tmdb, tag_id)
);

CREATE INDEX IF NOT EXISTS ix_media_tags_tag ON media_tags(tag_id);

CREATE TABLE IF NOT EXISTS release_restrictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    must_contain TEXT NOT NULL DEFAULT '',
    must_not_contain TEXT NOT NULL DEFAULT '',
    tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS root_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'any'
        CHECK (kind IN ('movie', 'series', 'anime', 'any')),
    path TEXT NOT NULL UNIQUE,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS series_settings (
    tmdb INTEGER PRIMARY KEY,
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id INTEGER REFERENCES root_folders(id) ON DELETE SET NULL,
    monitor_mode TEXT NOT NULL DEFAULT 'all'
        CHECK (monitor_mode IN ('all', 'future', 'missing', 'first_season', 'latest_season', 'pilot', 'none', 'manual')),
    series_type TEXT NOT NULL DEFAULT 'standard'
        CHECK (series_type IN ('standard', 'daily', 'anime')),
    season_folder INTEGER NOT NULL DEFAULT 1,
    language_override TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS custom_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    target TEXT NOT NULL DEFAULT 'catalog'
        CHECK (target IN ('catalog', 'wanted')),
    filters_json TEXT NOT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS import_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('tmdb_collection', 'tmdb_list', 'tmdb_popular', 'tmdb_discover', 'url')),
    source_ref TEXT NOT NULL,
    media_kind TEXT NOT NULL DEFAULT 'movie'
        CHECK (media_kind IN ('movie', 'series')),
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id INTEGER REFERENCES root_folders(id) ON DELETE SET NULL,
    tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    monitor_mode TEXT NOT NULL DEFAULT 'all',
    publish_immediately INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

ALTER TABLE media_items ADD COLUMN quality_profile_id INTEGER;
ALTER TABLE media_items ADD COLUMN root_folder_id INTEGER;
ALTER TABLE media_items ADD COLUMN language_override TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_profile ON media_items(quality_profile_id);
CREATE INDEX IF NOT EXISTS ix_media_items_root_folder ON media_items(root_folder_id);
