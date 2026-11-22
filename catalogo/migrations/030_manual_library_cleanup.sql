-- `library_added` significa ahora "está en Radarr/Sonarr". Las altas
-- manuales antiguas lo usaban para decir "solicitado en TOD"; se convierten
-- a seguimiento para no fingir pertenencia a *arr.
UPDATE media_items
SET library_added = 0,
    library_added_at = NULL,
    library_source = NULL
WHERE library_source IN ('manual', 'collection');
