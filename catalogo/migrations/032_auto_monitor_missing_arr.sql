-- Por defecto, todo lo que no está en Radarr/Sonarr queda en seguimiento.
-- Los nuevos títulos ya se marcan al insertarse; esto alcanza al catálogo
-- existente una sola vez. El barrido diario desmarca los que ya tienen .strm.
UPDATE media_items
SET monitored = 1,
    monitored_at = COALESCE(monitored_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    monitor_source = COALESCE(monitor_source, 'auto'),
    monitor_last_search_at = NULL
WHERE library_added = 0
  AND monitored = 0;
