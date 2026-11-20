ALTER TABLE media_items ADD COLUMN runtime_seconds INTEGER;

CREATE TABLE IF NOT EXISTS stream_failures (
    release_id INTEGER PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
    asset_id INTEGER,
    reason TEXT NOT NULL,
    until TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_stream_failures_until
    ON stream_failures(until);
