-- La búsqueda de respaldo en Radarr/Sonarr es un ajuste de búsqueda, no de siembra.
-- Se traslada la clave sin cambiar el valor guardado por el usuario.
INSERT OR IGNORE INTO settings(key, value)
SELECT 'search.arr_fallback', value FROM settings WHERE key = 'seed.arr_fallback';
DELETE FROM settings WHERE key = 'seed.arr_fallback';
