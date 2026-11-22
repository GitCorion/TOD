-- Altas: procedencia del catálogo y recepción de entradas.
--
-- `origin` responde a "¿por qué está esto aquí?" sin tocar la identidad ni
-- el estado de reproducción. `intake_batches`/`intake_items` guardan lo que
-- entró por cada vía, aunque fallara, para que la recepción sea real.

ALTER TABLE media_items ADD COLUMN origin TEXT;
ALTER TABLE media_items ADD COLUMN origin_detail TEXT;
ALTER TABLE media_items ADD COLUMN origin_at TEXT;

UPDATE media_items
SET origin = CASE
        WHEN library_source = 'radarr' THEN 'radarr'
        WHEN library_source = 'sonarr' THEN 'sonarr'
        WHEN monitor_source = 'rss' THEN 'rss'
        WHEN monitor_source IN ('tmdb', 'series') THEN 'tmdb'
        WHEN monitor_source = 'qbit' THEN 'qbit'
        WHEN monitor_source = 'collection' THEN 'collection'
        WHEN monitor_source = 'legacy' THEN 'legacy'
        ELSE 'auto'
    END,
    origin_at = COALESCE(library_added_at, created_at)
WHERE origin IS NULL;

CREATE INDEX IF NOT EXISTS ix_media_items_origin
    ON media_items(origin);

CREATE TABLE IF NOT EXISTS intake_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    label TEXT,
    state TEXT NOT NULL DEFAULT 'running'
        CHECK (state IN ('running', 'done', 'partial', 'error')),
    requested_by TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    duplicate INTEGER NOT NULL DEFAULT 0,
    needs_range INTEGER NOT NULL DEFAULT 0,
    unknown INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_intake_batches_created
    ON intake_batches(created_at DESC);

CREATE TABLE IF NOT EXISTS intake_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES intake_batches(id) ON DELETE CASCADE,
    raw TEXT NOT NULL,
    key TEXT,
    kind TEXT,
    tmdb INTEGER,
    season INTEGER,
    episode INTEGER,
    title TEXT,
    year INTEGER,
    state TEXT NOT NULL
        CHECK (state IN ('added', 'duplicate', 'needs_range', 'unknown', 'error')),
    detail TEXT,
    media_id INTEGER REFERENCES media_items(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_intake_items_batch
    ON intake_items(batch_id, id);
CREATE INDEX IF NOT EXISTS ix_intake_items_state
    ON intake_items(state);
