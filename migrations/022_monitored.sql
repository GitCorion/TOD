-- Seguimiento de títulos sin fuente: como la monitorización de Radarr/Sonarr.
ALTER TABLE media_items ADD COLUMN monitored INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media_items ADD COLUMN monitored_at TEXT;
ALTER TABLE media_items ADD COLUMN monitor_source TEXT;
ALTER TABLE media_items ADD COLUMN monitor_last_search_at TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_monitored
    ON media_items(monitored) WHERE monitored = 1;
