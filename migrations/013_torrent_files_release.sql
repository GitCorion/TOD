-- Vincula ficheros ya descargados con su release para resolver versiones.
UPDATE torrent_files
SET release_id = (
    SELECT t.release_id FROM torrents t WHERE t.id = torrent_files.torrent_id
)
WHERE release_id IS NULL AND torrent_id IS NOT NULL;
