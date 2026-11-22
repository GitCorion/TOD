ALTER TABLE media_items ADD COLUMN stream_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media_items ADD COLUMN stream_switched_at TEXT;
ALTER TABLE media_items ADD COLUMN stream_switch_reason TEXT;
