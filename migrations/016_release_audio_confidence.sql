-- De dónde sale el idioma del release: etiqueta del título o tracker.
ALTER TABLE releases ADD COLUMN audio_confidence TEXT
    CHECK (
        audio_confidence IS NULL
        OR audio_confidence IN ('title', 'tracker')
    );
