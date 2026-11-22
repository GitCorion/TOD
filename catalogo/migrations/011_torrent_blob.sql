-- El enlace de Jackett puede caducar; guardamos el .torrent en el sync.
ALTER TABLE releases ADD COLUMN torrent_blob BLOB;
