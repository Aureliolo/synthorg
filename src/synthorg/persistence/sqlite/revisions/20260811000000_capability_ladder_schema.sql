-- The capability ladder's two schema changes: a column that said size
-- where it meant capability, and the table that lets evidence correct it.
--
-- 1. The pin-validation row records a capability, and its column said size.
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
--
-- 2. Externally-sourced capability evidence gets somewhere to live.
--
-- One row per (source_label, model_identifier, axis): what one published
-- source measured about one model. ``model_identifier`` is the source's own
-- string, kept verbatim so an unresolved row stays inspectable rather than
-- vanishing into a failed match. ``as_of`` is when the SOURCE measured it
-- and is what staleness is read from; ``ingested_at`` is when we read the
-- source. A refresh upserts and never bulk-deletes, so a feed that drops a
-- model or fails outright leaves its last good row ageing visibly rather
-- than silently un-grading the model.

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
    END
FROM model_pin_validations;

DROP TABLE model_pin_validations;

ALTER TABLE model_pin_validations_new RENAME TO model_pin_validations;

CREATE TABLE model_capability_scores (
    source_label TEXT NOT NULL CHECK (LENGTH(TRIM(source_label)) > 0),
    model_identifier TEXT NOT NULL CHECK (LENGTH(TRIM(model_identifier)) > 0),
    axis TEXT NOT NULL CHECK (axis IN ('coding', 'reasoning', 'general')),
    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
    as_of TEXT NOT NULL CHECK (as_of LIKE '%+00:00' OR as_of LIKE '%Z'),
    ingested_at TEXT NOT NULL CHECK (
        ingested_at LIKE '%+00:00' OR ingested_at LIKE '%Z'
    ),
    PRIMARY KEY (source_label, model_identifier, axis)
);

CREATE INDEX idx_model_capability_scores_model
ON model_capability_scores (model_identifier, axis);
CREATE INDEX idx_model_capability_scores_source
ON model_capability_scores (source_label, as_of DESC);
