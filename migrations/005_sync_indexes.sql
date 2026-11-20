-- Índices para el Sync y el mapeo de packs.
CREATE INDEX IF NOT EXISTS ix_torrent_files_torrent_file
    ON torrent_files(torrent_id, file_index);
CREATE INDEX IF NOT EXISTS ix_media_items_tvdb
    ON media_items(tvdb_id);
