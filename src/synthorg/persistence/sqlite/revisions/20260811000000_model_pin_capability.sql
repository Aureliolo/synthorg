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
--
-- SQLite cannot alter a CHECK, so the table is rebuilt (create-new, copy,
-- drop, rename). Nothing references it by foreign key, so the drop fires no
-- cascades.

CREATE TABLE model_pin_validations_new (
    prompt_class_id TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(prompt_class_id)) > 0),
    validated_at TEXT NOT NULL,
    capability TEXT NOT NULL CHECK (capability IN ('basic', 'capable', 'expert'))
);

INSERT INTO model_pin_validations_new (
    prompt_class_id, validated_at, capability
)
SELECT
    prompt_class_id,
    validated_at,
    CASE tier
        WHEN 'large' THEN 'expert'
        WHEN 'medium' THEN 'capable'
        WHEN 'small' THEN 'basic'
        WHEN 'local-small' THEN 'basic'
        ELSE tier
    END AS capability
FROM model_pin_validations;

DROP TABLE model_pin_validations;

ALTER TABLE model_pin_validations_new RENAME TO model_pin_validations;
