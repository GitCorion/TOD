-- Ficheros reales que ya están en Radarr/Sonarr. El proxy los usa como
-- fuente preferente para los .strm estables.
CREATE TABLE library_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('movie', 'series')),
    tmdb INTEGER NOT NULL,
    season INTEGER NOT NULL DEFAULT 0,
    episode INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    size_bytes INTEGER,
    source TEXT NOT NULL CHECK (source IN ('radarr', 'sonarr')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX ux_library_files_key
    ON library_files(kind, tmdb, season, episode);
CREATE INDEX ix_library_files_tmdb ON library_files(tmdb);
