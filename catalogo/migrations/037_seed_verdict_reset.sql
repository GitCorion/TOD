-- Los primeros veredictos miraban todos los trackers del .torrent y podían
-- culpar al tracker equivocado (un público sin ese torrent). Se limpian
-- para que el siguiente ciclo los recalcule solo con el tracker del release.
UPDATE seed_commitments
SET tracker_verdict = NULL,
    tracker_message = NULL,
    tracker_checked_at = NULL;
