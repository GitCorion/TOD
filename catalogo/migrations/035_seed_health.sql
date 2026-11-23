-- El mínimo de siembra se cuenta desde que la descarga termina.
-- `started_at` es el inicio de la descarga; no sirve como inicio de seed.
ALTER TABLE seed_commitments ADD COLUMN seed_started_at TEXT;
ALTER TABLE seed_commitments ADD COLUMN tracker_verdict TEXT;
ALTER TABLE seed_commitments ADD COLUMN tracker_message TEXT;
ALTER TABLE seed_commitments ADD COLUMN tracker_checked_at TEXT;

-- Los plazos antiguos se calcularon desde la descarga: se invalidan y
-- seeding los recalcula con el inicio real de siembra.
UPDATE seed_commitments
SET seed_required_until = NULL
WHERE state IN ('approved', 'active', 'seeding');
