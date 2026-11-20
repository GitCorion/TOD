-- Normaliza storage_location por prefijo real de ruta.
-- La migración 004 marcaba como 1FICHIER cualquier library_path; no todas
-- las rutas de biblioteca están en el mount de archivo.

UPDATE torrent_files
SET storage_location = CASE
    WHEN library_path LIKE '/mnt/archivo/%' THEN '1FICHIER'
    WHEN local_path IS NOT NULL AND local_path <> '' THEN 'LOCAL_DISK'
    WHEN library_path IS NOT NULL AND library_path <> '' THEN 'LOCAL_DISK'
    ELSE 'NONE'
END
WHERE library_path IS NOT NULL OR local_path IS NOT NULL;

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
END
WHERE EXISTS (
    SELECT 1 FROM torrent_files f WHERE f.asset_id = media_items.id
);
