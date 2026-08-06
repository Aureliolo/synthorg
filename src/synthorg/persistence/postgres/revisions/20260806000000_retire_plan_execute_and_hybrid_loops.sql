-- Rewrite settings rows naming an inner loop that no longer exists.
--
-- The plan_execute and hybrid loops decomposed a work unit a second time
-- inside the agent, below every governance surface the plan-review gate
-- provides, and are gone. React runs in their place: it is the only loop
-- that needs no provisioning, unlike openhands with its sandbox image and
-- gateway boundaries.
--
-- Without this, a row written while those names were valid outlives them.
-- A setting is validated on write and never on read, so the stale string
-- reaches AutoLoopConfig unchanged and raises there, inside the runtime
-- rebuild that ~30 unrelated watched keys also trigger.
--
-- updated_at is deliberately left alone: it records when the operator last
-- chose a value, which is still true. This corrects a name, it is not an
-- operator edit.

UPDATE settings
SET value = 'react'
WHERE
    namespace = 'engine'
    AND key = 'default_loop_type'
    AND value IN ('plan_execute', 'hybrid');

UPDATE settings
SET value = REPLACE(REPLACE(value, ':plan_execute', ':react'), ':hybrid', ':react')
WHERE
    namespace = 'engine'
    AND key = 'loop_complexity_overrides'
    AND (value LIKE '%:plan_execute%' OR value LIKE '%:hybrid%');
