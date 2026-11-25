-- Metadatos enriquecidos de releases y catálogo al estilo Sonarr/Radarr.

ALTER TABLE releases ADD COLUMN source_type TEXT;
ALTER TABLE releases ADD COLUMN hdr TEXT;
ALTER TABLE releases ADD COLUMN audio_codec TEXT;
ALTER TABLE releases ADD COLUMN audio_channels TEXT;
ALTER TABLE releases ADD COLUMN edition TEXT;
ALTER TABLE releases ADD COLUMN release_group TEXT;
ALTER TABLE releases ADD COLUMN proper_rank INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN streaming_service TEXT;
ALTER TABLE releases ADD COLUMN parsed_flags TEXT;

ALTER TABLE media_items ADD COLUMN genres TEXT;
ALTER TABLE media_items ADD COLUMN series_status TEXT;
ALTER TABLE media_items ADD COLUMN series_type TEXT NOT NULL DEFAULT 'standard'
    CHECK (series_type IN ('standard', 'daily', 'anime'));
ALTER TABLE media_items ADD COLUMN imdb_id TEXT;
ALTER TABLE media_items ADD COLUMN certification TEXT;
ALTER TABLE media_items ADD COLUMN network_studio TEXT;
ALTER TABLE media_items ADD COLUMN in_cinemas_date TEXT;
ALTER TABLE media_items ADD COLUMN digital_release_date TEXT;
ALTER TABLE media_items ADD COLUMN physical_release_date TEXT;
ALTER TABLE media_items ADD COLUMN minimum_availability TEXT NOT NULL DEFAULT 'released'
    CHECK (minimum_availability IN ('announced', 'in_cinemas', 'released'));
ALTER TABLE media_items ADD COLUMN is_anime INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_releases_quality_tier
    ON releases(quality, source_type, codec, audio);
CREATE INDEX IF NOT EXISTS ix_media_items_anime
    ON media_items(is_anime, kind);
