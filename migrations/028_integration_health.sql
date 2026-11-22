-- Último estado conocido de cada integración, para que Inicio no tenga que
-- esperar a la red. Lo escribe la tarea `attention-check` y lo lee /api/attention.

CREATE TABLE IF NOT EXISTS integration_health (
    service TEXT PRIMARY KEY,
    ok INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    latency_ms INTEGER,
    detail TEXT,
    error TEXT,
    hint TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    since TEXT
);

CREATE INDEX IF NOT EXISTS idx_integration_health_checked
    ON integration_health(checked_at);
