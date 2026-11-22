-- Compromisos de descarga/seed.
-- Una descarga no debe empezar sin una fila aquí que fije el tiempo/ratio
-- que hay que cumplir después.

CREATE TABLE IF NOT EXISTS download_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
    tracker TEXT NOT NULL,
    info_hash TEXT,
    size_bytes INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'approved'
        CHECK (state IN ('approved', 'active', 'seeding', 'done',
                         'cancelled', 'failed')),
    reason TEXT,
    seed_required_until TEXT,
    target_ratio REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    approved_at TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_download_intents_asset
    ON download_intents(asset_id);
CREATE INDEX IF NOT EXISTS ix_download_intents_tracker_state
    ON download_intents(tracker, state);
CREATE UNIQUE INDEX IF NOT EXISTS ux_download_intents_release_active
    ON download_intents(release_id)
    WHERE release_id IS NOT NULL
      AND state IN ('approved', 'active', 'seeding');
