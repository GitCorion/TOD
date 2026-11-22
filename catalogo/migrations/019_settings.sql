-- Ajustes editables del catálogo on-demand.
-- Se separan de las variables de entorno para poder cambiarlos sin reiniciar.

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
