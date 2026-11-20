-- Caché de debrid para releases públicos.
ALTER TABLE releases ADD COLUMN debrid_provider TEXT;
ALTER TABLE releases ADD COLUMN debrid_cached INTEGER;
ALTER TABLE releases ADD COLUMN debrid_checked_at TEXT;
ALTER TABLE releases ADD COLUMN debrid_torrent_id TEXT;
ALTER TABLE releases ADD COLUMN debrid_link TEXT;
ALTER TABLE releases ADD COLUMN debrid_error TEXT;

CREATE INDEX IF NOT EXISTS ix_releases_debrid_cached
    ON releases(debrid_cached);
