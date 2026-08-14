-- Carry the capability vocabulary into the rows already written under it.
--
-- A setting is validated on write and never on read, so a row written while
-- the old names were valid outlives them. Renaming a key in code does not
-- touch the settings table: the resolver queries a name no row has, finds
-- nothing, and falls through to the code default. That is the quietest
-- possible failure, because it reads exactly like "the operator never
-- configured this" -- no exception, no warning, not even a log line.
--
-- 1. The pin-validation row records a capability, and its column said size.
--
-- ``ModelPinValidationRow.capability`` is typed ``CapabilityLevel``, so the
-- value the benchmark writes is ``basic`` / ``capable`` / ``expert``. The
-- column admitted only the size vocabulary the ladder used to carry, so every
-- write after the regrade would have been refused by the CHECK.
--
-- The three old sizes map onto the three rungs one for one. ``local-small``
-- mixed two axes (how capable a model is, and where it runs); as a rung it was
-- only ever a claim about capability, so it lands on ``basic`` and locality is
-- carried by the signal the matcher derives from the base URL.
--
-- SQLite cannot alter a CHECK, so the table is rebuilt (create-new, copy,
-- drop, rename). Nothing references it by foreign key, so the drop fires no
-- cascades. The ELSE arm is unreachable under the old CHECK and is kept
-- deliberately: an unmapped value passes through and then fails the new CHECK,
-- so the migration stops loudly rather than coercing to a default.
--
-- 2. Twelve setting keys moved with the ladder.
--
-- Each rename is applied to the stored row so the new key reads the operator's
-- own value. Losing one changes behaviour silently: an orphaned
-- ``capability_overrides`` reverts every hand-corrected model to the heuristic
-- classification, and an orphaned ``model_capability_overrides`` reverts the
-- budget downgrade map.
--
-- 3. Three stored values speak the vocabulary too, not just the keys.
--
-- ``company.agents`` is the load-bearing one. ``AgentConfig`` and
-- ``ModelConfig`` are both ``extra="forbid"``, so a roster still carrying
-- ``tier`` / ``model_tier`` fails validation for every agent, and the resolver
-- answers a failed roster read with the code default: the operator's whole
-- company would silently read as empty.
--
-- Both JSON spellings are rewritten because the two writers disagree:
-- ``model_dump_json`` emits compact separators, ``json.dumps`` emits a space
-- after the colon. Rung values are matched together with the key that owns
-- them (rather than on the bare value) so a model id that happens to be a rung
-- word is never rewritten; ``model_capability_overrides`` is the one exception,
-- because there every value is a rung by schema and the keys are model ids.

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

UPDATE settings SET key = 'model_capability_overrides'
WHERE namespace = 'budget' AND key = 'model_tier_overrides';

UPDATE settings SET key = 'forecast_static_prior_per_turn_expert'
WHERE namespace = 'budget' AND key = 'forecast_static_prior_per_turn_large';

UPDATE settings SET key = 'forecast_static_prior_per_turn_capable'
WHERE namespace = 'budget' AND key = 'forecast_static_prior_per_turn_medium';

UPDATE settings SET key = 'forecast_static_prior_per_turn_basic'
WHERE namespace = 'budget' AND key = 'forecast_static_prior_per_turn_small';

UPDATE settings SET key = 'forecast_static_prior_per_turn_local'
WHERE namespace = 'budget' AND key = 'forecast_static_prior_per_turn_local_small';

UPDATE settings SET key = 'model_spend_profile'
WHERE namespace = 'company' AND key = 'model_tier_profile';

UPDATE settings SET key = 'matcher_expert_min_context'
WHERE namespace = 'engine' AND key = 'matcher_tier_large_min_context';

UPDATE settings SET key = 'matcher_capable_min_context'
WHERE namespace = 'engine' AND key = 'matcher_tier_medium_min_context';

UPDATE settings SET key = 'matcher_min_cloud_cost_tier'
WHERE namespace = 'engine' AND key = 'matcher_min_cloud_tier';

UPDATE settings SET key = 'capability_overrides'
WHERE namespace = 'providers' AND key = 'tier_assignment_overrides';

UPDATE settings SET key = 'capability_classifier_model'
WHERE namespace = 'providers' AND key = 'tier_classifier_model';

UPDATE settings SET key = 'capability_classifier_enabled'
WHERE namespace = 'providers' AND key = 'tier_classifier_enabled';

UPDATE settings SET value = REPLACE(value, '"tier":', '"capability":')
WHERE namespace = 'providers' AND key = 'capability_overrides';

UPDATE settings SET value = REPLACE(value, '"tier" :', '"capability" :')
WHERE namespace = 'providers' AND key = 'capability_overrides';

UPDATE settings SET value = REPLACE(value, '"model_tier"', '"capability"')
WHERE namespace = 'company' AND key = 'agents';

UPDATE settings SET value = REPLACE(value, '"tier"', '"capability"')
WHERE namespace = 'company' AND key = 'agents';

UPDATE settings
SET value = REPLACE(value, '"capability":"local-small"', '"capability":"basic"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability": "local-small"', '"capability": "basic"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability":"large"', '"capability":"expert"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability": "large"', '"capability": "expert"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability":"medium"', '"capability":"capable"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability": "medium"', '"capability": "capable"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability":"small"', '"capability":"basic"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings
SET value = REPLACE(value, '"capability": "small"', '"capability": "basic"')
WHERE namespace IN ('company', 'providers') AND key IN ('agents', 'capability_overrides');

UPDATE settings SET value = REPLACE(value, ':"local-small"', ':"basic"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ': "local-small"', ': "basic"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ':"large"', ':"expert"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ': "large"', ': "expert"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ':"medium"', ':"capable"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ': "medium"', ': "capable"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ':"small"', ':"basic"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';

UPDATE settings SET value = REPLACE(value, ': "small"', ': "basic"')
WHERE namespace = 'budget' AND key = 'model_capability_overrides';
