-- Métricas de arranque de reproducción por intención.
-- Permite medir preparación y primer byte sin tocar el estado del título.
ALTER TABLE play_intents ADD COLUMN first_byte_at TEXT;
