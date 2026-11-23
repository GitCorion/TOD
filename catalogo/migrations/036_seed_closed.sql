-- Un compromiso cancelado o fallido ya no tiene obligación de siembra.
-- Los plazos antiguos calculados desde la descarga no deben mostrarse.
UPDATE seed_commitments
SET seed_required_until = NULL
WHERE state IN ('cancelled', 'failed');
