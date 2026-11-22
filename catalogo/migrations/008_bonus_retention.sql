-- Retención por bonus: el mínimo de 72h no es el objetivo; es solo el suelo.
ALTER TABLE tracker_policies
    ADD COLUMN keep_for_bonus INTEGER NOT NULL DEFAULT 1;
