-- Leases de reproducción: permiten mover a 1fichier solo si no hay stream.
CREATE TABLE IF NOT EXISTS stream_leases (
    media_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    client_ip TEXT,
    user_agent TEXT,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (media_id, session_id)
);

CREATE INDEX IF NOT EXISTS ix_stream_leases_last_seen
    ON stream_leases(last_seen);
