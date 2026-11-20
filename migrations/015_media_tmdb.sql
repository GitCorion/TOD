-- Metadatos TMDB para buscar en trackers por título original.
ALTER TABLE media_items ADD COLUMN original_title TEXT;
ALTER TABLE media_items ADD COLUMN original_language TEXT;
ALTER TABLE media_items ADD COLUMN aliases TEXT;
ALTER TABLE media_items ADD COLUMN popularity REAL;
ALTER TABLE media_items ADD COLUMN external_source TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_popularity
    ON media_items(popularity DESC);
