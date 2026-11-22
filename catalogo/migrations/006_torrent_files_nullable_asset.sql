-- Los ficheros de un pack pueden no tener un media_item asociado.
-- SQLite no permite quitar NOT NULL con ALTER, así que se reconstruye la tabla.
PRAGMA foreign_keys = OFF;
BEGIN;

CREATE TABLE torrent_files_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER REFERENCES media_items(id) ON DELETE SET NULL,
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
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    storage_location TEXT NOT NULL DEFAULT 'NONE'
        CHECK (storage_location IN ('NONE', 'LOCAL_DISK', '1FICHIER')),
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    cloud_uploaded_at TEXT,
    priority INTEGER NOT NULL DEFAULT 0
);

INSERT INTO torrent_files_new (
    id, asset_id, release_id, torrent_id, legacy_file_id, file_index,
    rel_path, local_path, library_path, size_bytes, state, media_info,
    last_seen, created_at, updated_at, storage_location, downloaded_bytes,
    cloud_uploaded_at, priority
)
SELECT
    id, asset_id, release_id, torrent_id, legacy_file_id, file_index,
    rel_path, local_path, library_path, size_bytes, state, media_info,
    last_seen, created_at, updated_at, storage_location, downloaded_bytes,
    cloud_uploaded_at, priority
FROM torrent_files;

DROP TABLE torrent_files;
ALTER TABLE torrent_files_new RENAME TO torrent_files;

CREATE INDEX ix_files_asset ON torrent_files(asset_id);
CREATE INDEX ix_files_state ON torrent_files(state);
CREATE INDEX ix_torrent_files_storage ON torrent_files(storage_location);
CREATE INDEX ix_torrent_files_cloud ON torrent_files(cloud_uploaded_at);
CREATE INDEX ix_torrent_files_torrent_file
    ON torrent_files(torrent_id, file_index);

COMMIT;
PRAGMA foreign_keys = ON;
