CREATE TABLE IF NOT EXISTS swarm_stats (
    release_id INTEGER PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
    tracker TEXT,
    connected_seeds INTEGER NOT NULL DEFAULT 0,
    swarm_seeds INTEGER NOT NULL DEFAULT 0,
    speed_bps INTEGER NOT NULL DEFAULT 0,
    buffer_bytes INTEGER NOT NULL DEFAULT 0,
    required_speed_bps INTEGER NOT NULL DEFAULT 0,
    started_ok INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
