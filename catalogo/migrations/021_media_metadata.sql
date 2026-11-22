-- Metadatos visuales para el catálogo (póster, sinopsis, nota).
-- Son datos de presentación: no afectan a la resolución ni a la salud.
ALTER TABLE media_items ADD COLUMN poster_path TEXT;
ALTER TABLE media_items ADD COLUMN backdrop_path TEXT;
ALTER TABLE media_items ADD COLUMN overview TEXT;
ALTER TABLE media_items ADD COLUMN vote_average REAL;
ALTER TABLE media_items ADD COLUMN metadata_at TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_metadata_pending
    ON media_items(metadata_at) WHERE metadata_at IS NULL;
