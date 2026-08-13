-- The pin-validation row records a capability, and its column said size.
--
-- ``ModelPinValidationRow.tier`` is typed ``CapabilityLevel``, so the value
-- the benchmark writes is ``basic`` / ``capable`` / ``expert``. The column
-- admitted only the size vocabulary the ladder used to carry, which means
-- every write after the ladder was regraded would have been refused by the
-- CHECK rather than persisted.
--
-- The three old sizes map onto the three rungs one for one. ``local-small``
-- mixed two axes (how capable a model is, and where it runs); as a rung it
-- was only ever a claim about capability, so it lands on ``basic`` and the
-- locality half is carried by the signal the matcher derives from the base
-- URL.

ALTER TABLE model_pin_validations DROP CONSTRAINT model_pin_validations_tier_check;

ALTER TABLE model_pin_validations RENAME COLUMN tier TO capability;

UPDATE model_pin_validations
SET capability = CASE capability
    WHEN 'large' THEN 'expert'
    WHEN 'medium' THEN 'capable'
    WHEN 'small' THEN 'basic'
    WHEN 'local-small' THEN 'basic'
    ELSE capability
END;

ALTER TABLE model_pin_validations
ADD CONSTRAINT model_pin_validations_capability_check
CHECK (capability IN ('basic', 'capable', 'expert'));
