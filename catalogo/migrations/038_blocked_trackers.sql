-- Un tracker bloqueado no puede ofrecer releases vivas.
--
-- El bloqueo marcaba los releases existentes, pero el siguiente sync los
-- volvía a poner en 'alive'. Se corrige lo ya guardado y se impide por
-- trigger, para que ningún escritor (bulk_sync, prowlarr_sync, health...)
-- pueda saltarse la decisión del usuario.

UPDATE releases
SET health = 'blocked'
WHERE health <> 'blocked'
  AND tracker IN (SELECT tracker FROM tracker_policies WHERE enabled = 0);

CREATE TRIGGER IF NOT EXISTS trg_releases_blocked_insert
AFTER INSERT ON releases
WHEN EXISTS (
    SELECT 1 FROM tracker_policies
    WHERE tracker = NEW.tracker AND enabled = 0
)
BEGIN
    UPDATE releases SET health = 'blocked' WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_releases_blocked_update
AFTER UPDATE OF health, tracker ON releases
WHEN NEW.health <> 'blocked'
  AND EXISTS (
      SELECT 1 FROM tracker_policies
      WHERE tracker = NEW.tracker AND enabled = 0
  )
BEGIN
    UPDATE releases SET health = 'blocked' WHERE id = NEW.id;
END;

-- El estado `enabled` de la política es la fuente de verdad, se cambie
-- desde el botón de bloqueo o desde la tabla de Siembra.
CREATE TRIGGER IF NOT EXISTS trg_policy_blocked_insert
AFTER INSERT ON tracker_policies
WHEN NEW.enabled = 0
BEGIN
    UPDATE releases SET health = 'blocked'
    WHERE tracker = NEW.tracker AND health <> 'blocked';
END;

CREATE TRIGGER IF NOT EXISTS trg_policy_blocked_update
AFTER UPDATE OF enabled ON tracker_policies
WHEN NEW.enabled = 0
BEGIN
    UPDATE releases SET health = 'blocked'
    WHERE tracker = NEW.tracker AND health <> 'blocked';
END;

CREATE TRIGGER IF NOT EXISTS trg_policy_enabled_update
AFTER UPDATE OF enabled ON tracker_policies
WHEN NEW.enabled = 1
BEGIN
    UPDATE releases SET health = 'unknown'
    WHERE tracker = NEW.tracker AND health = 'blocked';
END;
