-- Abandono de descargas que no llegan al 5% si nadie las reproduce.
ALTER TABLE tracker_policies
    ADD COLUMN abandon_under_percent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tracker_policies
    ADD COLUMN abandon_action TEXT NOT NULL DEFAULT 'keep'
        CHECK (abandon_action IN ('keep', 'pause', 'delete'));
