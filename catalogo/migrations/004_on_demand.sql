-- Migración 004: alinear el modelo con el sistema on-demand.
--
-- Renombrados:
--   assets            -> media_items
--   files             -> torrent_files
--   download_intents  -> seed_commitments
--
-- Añade la identidad pública (media_id), el estado de máquina de estados,
-- los metadatos de qBittorrent y los play_intents.

ALTER TABLE assets RENAME TO media_items;
ALTER TABLE files RENAME TO torrent_files;
ALTER TABLE download_intents RENAME TO seed_commitments;

ALTER TABLE media_items ADD COLUMN media_id TEXT;
ALTER TABLE media_items ADD COLUMN tvdb_id INTEGER;
ALTER TABLE media_items ADD COLUMN status TEXT NOT NULL DEFAULT 'COLD'
    CHECK (status IN ('COLD', 'ACTIVATING', 'DOWNLOADING_LOCAL',
                      'SEEDING_LOCAL', 'MOVING_TO_1FICHIER', 'ON_1FICHIER'));
ALTER TABLE media_items ADD COLUMN active_file_id INTEGER;

ALTER TABLE torrents ADD COLUMN category TEXT;
ALTER TABLE torrents ADD COLUMN qbit_state TEXT;
ALTER TABLE torrents ADD COLUMN progress REAL NOT NULL DEFAULT 0;
ALTER TABLE torrents ADD COLUMN base_path TEXT;
ALTER TABLE torrents ADD COLUMN content_path TEXT;

ALTER TABLE torrent_files ADD COLUMN storage_location TEXT NOT NULL DEFAULT 'NONE'
    CHECK (storage_location IN ('NONE', 'LOCAL_DISK', '1FICHIER'));
ALTER TABLE torrent_files ADD COLUMN downloaded_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE torrent_files ADD COLUMN cloud_uploaded_at TEXT;
ALTER TABLE torrent_files ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS ux_media_items_media_id
    ON media_items(media_id);
CREATE INDEX IF NOT EXISTS ix_media_items_status
    ON media_items(status);
CREATE INDEX IF NOT EXISTS ix_torrents_category
    ON torrents(category);
CREATE INDEX IF NOT EXISTS ix_torrent_files_storage
    ON torrent_files(storage_location);
CREATE INDEX IF NOT EXISTS ix_torrent_files_cloud
    ON torrent_files(cloud_uploaded_at);

-- Intención de reproducción. Separada del compromiso de seed.
CREATE TABLE IF NOT EXISTS play_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'range'
        CHECK (source IN ('range', 'webhook', 'manual')),
    client_ip TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_play_intents_media
    ON play_intents(media_id, consumed_at);
CREATE INDEX IF NOT EXISTS ix_play_intents_expiry
    ON play_intents(expires_at);

-- Backfill de identidad pública.
UPDATE media_items
SET media_id = key
WHERE media_id IS NULL;

-- Backfill de localización real.
UPDATE torrent_files
SET storage_location = CASE
    WHEN library_path IS NOT NULL AND library_path <> '' THEN '1FICHIER'
    WHEN local_path IS NOT NULL AND local_path <> '' THEN 'LOCAL_DISK'
    ELSE 'NONE'
END
WHERE storage_location = 'NONE';

-- Backfill de máquina de estados.
UPDATE media_items
SET status = CASE
    WHEN EXISTS (
        SELECT 1 FROM torrent_files f
        WHERE f.asset_id = media_items.id
          AND f.storage_location = '1FICHIER'
          AND f.state IN ('archived', 'ready', 'seeding')
    ) THEN 'ON_1FICHIER'
    WHEN EXISTS (
        SELECT 1 FROM torrent_files f
        WHERE f.asset_id = media_items.id
          AND f.local_path IS NOT NULL
          AND f.state IN ('ready', 'seeding')
    ) THEN 'SEEDING_LOCAL'
    WHEN EXISTS (
        SELECT 1 FROM torrent_files f
        WHERE f.asset_id = media_items.id
          AND f.state = 'downloading'
    ) THEN 'DOWNLOADING_LOCAL'
    ELSE 'COLD'
END;

-- El puntero existente pasa a active_file_id.
UPDATE media_items
SET active_file_id = (
    SELECT p.file_id FROM play_targets p
    WHERE p.asset_id = media_items.id
      AND p.file_id IS NOT NULL
)
WHERE active_file_id IS NULL;
