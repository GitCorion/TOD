-- Pertenencia a la biblioteca independiente de que exista .strm.
ALTER TABLE media_items ADD COLUMN library_added INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media_items ADD COLUMN library_added_at TEXT;
ALTER TABLE media_items ADD COLUMN library_source TEXT;

CREATE INDEX IF NOT EXISTS ix_media_items_library_added
    ON media_items(library_added) WHERE library_added = 1;
