-- El espejo de Radarr/Sonarr no debe marcar todos sus títulos como
-- "monitorizados" en TOD: el *arr ya los monitoriza. La búsqueda de fuentes
-- de TOD se guía por library_added y el job arr-mirror.
UPDATE media_items
SET monitored = 0,
    monitor_source = NULL
WHERE library_source IN ('radarr', 'sonarr')
  AND monitor_source IN ('radarr', 'sonarr');
