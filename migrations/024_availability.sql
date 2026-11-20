-- Fechas de disponibilidad para no buscar antes de estreno/emision.
ALTER TABLE media_items ADD COLUMN release_date TEXT;
ALTER TABLE media_items ADD COLUMN air_date TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_monitor_available
    ON media_items(monitored, release_date, air_date)
    WHERE monitored = 1;
