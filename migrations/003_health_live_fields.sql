-- Datos de la última observación de Prowlarr, para auditar sin volver a buscar.
ALTER TABLE releases ADD COLUMN live_title TEXT;
ALTER TABLE releases ADD COLUMN live_tracker TEXT;
ALTER TABLE releases ADD COLUMN live_guid TEXT;
ALTER TABLE releases ADD COLUMN live_publish_date TEXT;
