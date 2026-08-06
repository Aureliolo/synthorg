-- Rewrite settings rows naming an inner loop that no longer exists.
--
-- The plan_execute and hybrid loops decomposed a work unit a second time
-- inside the agent, below every governance surface the plan-review gate
-- provides, and are gone. React runs in their place: it is the only loop
-- that needs no provisioning, unlike openhands with its sandbox image and
-- gateway boundaries.
--
-- A setting is validated on write and never on read, so a row written while
-- those names were valid outlives them. resolve_loop_type() keeps such a row
-- runnable by substituting react at every read, which is what the dashboard
-- cannot show: the operator reads back the dead name they stored and has no
-- way to tell it is not the loop running. This corrects the stored value so
-- the two agree.
--
-- updated_at is deliberately left alone. Beyond recording when the operator
-- last chose a value, which is still true, it is the optimistic-concurrency
-- token for set_if_unchanged, so bumping it here would fail an unrelated
-- in-flight settings write that is holding a pre-migration token.

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
