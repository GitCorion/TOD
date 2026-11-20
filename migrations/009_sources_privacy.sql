-- Privacidad/fuente de releases y políticas; fallback público.
ALTER TABLE releases ADD COLUMN source TEXT NOT NULL DEFAULT 'prowlarr';
ALTER TABLE releases ADD COLUMN privacy TEXT NOT NULL DEFAULT 'private';
ALTER TABLE tracker_policies ADD COLUMN privacy TEXT NOT NULL DEFAULT 'private';
ALTER TABLE tracker_policies ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS ix_releases_source ON releases(source);
CREATE INDEX IF NOT EXISTS ix_releases_privacy ON releases(privacy);
