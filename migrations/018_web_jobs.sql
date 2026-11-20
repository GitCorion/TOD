-- Tareas de administración lanzadas desde el panel web.
-- Persistentes para sobrevivir a reinicios y poder auditar qué se ejecutó.

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    params TEXT,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'ok', 'error', 'cancelled')),
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    result TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    requested_by TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_jobs_state_created
    ON jobs(state, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_jobs_kind_state
    ON jobs(kind, state);
