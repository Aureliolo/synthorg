-- Five schema facts, one revision.
--
-- 1. The project's human name is denormalised onto the plan.
-- 2. The capability rename reaches the identity ARCHIVE, not just the roster.
-- 3. The roster's second copy of the capability rung is dropped.
-- 4. A latching provider refusal gets somewhere durable to live.
-- 5. A charter's approval stops depending on the run it authorises.
--
-- ── 1. plans.project_name ─────────────────────────────────────
--
-- Every plan surface (the review inbox row, the detail header, the chat card
-- that links to it) had only plans.project, which is a project id. An id is a
-- database key, not information: it is not memorable, not comparable by eye,
-- and it crowds out the name it stands in for. This is the same denormalisation
-- objective_title already carries, for the same stated reason -- so the surface
-- never has to resolve an id, and never falls back to showing one.

ALTER TABLE plans
ADD COLUMN project_name TEXT NOT NULL DEFAULT '';

-- Backfill from the projects table where the plan's project still resolves.
-- A plan whose project is gone gets a word rather than its id: the column is
-- what a surface prints, and an id printed under the heading "project" is the
-- defect this column was added to remove. Nothing is lost by not repeating it,
-- since plans.project still carries the key.
UPDATE plans SET project_name = projects.name
FROM projects
WHERE plans.project = projects.id AND plans.project_name = '';

UPDATE plans SET project_name = 'Unknown project'
WHERE project_name = '';

-- project_name carries the same non-blank guard as its sibling name column.
-- The transient '' default (needed to add the NOT NULL column to existing rows)
-- is dropped now the backfill guarantees every row is non-blank.
ALTER TABLE plans ALTER COLUMN project_name DROP DEFAULT;
-- Added NOT VALID then validated separately: the backfill above already
-- guarantees non-blank values, so a validating scan under the ALTER's lock is
-- avoidable. VALIDATE takes only a SHARE UPDATE EXCLUSIVE lock, so concurrent
-- reads and writes on a hot plans table are not blocked.
ALTER TABLE plans
ADD CONSTRAINT plans_project_name_check
CHECK (CHAR_LENGTH(TRIM(project_name)) > 0) NOT VALID;
ALTER TABLE plans VALIDATE CONSTRAINT plans_project_name_check;

-- ── 2. The identity archive speaks the capability vocabulary ──
--
-- 20260811000000_capability_vocabulary renamed model_tier to capability and
-- rewrote every stored value that spoke the old vocabulary. It gave the reason
-- itself: AgentConfig and ModelConfig are extra="forbid", so a stored roster
-- still carrying model_tier fails validation for every agent. It applied that
-- to settings (company.agents) and to model_pin_validations, and not to
-- agent_identity_versions, which stores the same AgentIdentity objects under
-- the same forbid.
--
-- So every snapshot ever taken is unreadable, and reads degrade to a warning:
--
--   persistence.version.fetch_failed  table=agent_identity_versions
--     reason=schema_drift
--     error="2 validation errors for AgentIdentity
--            model.model_tier      Extra inputs are not permitted
--            model.fallback_model  Extra inputs are not permitted"
--
-- fallback_model goes rather than moves: ModelConfig deleted it outright when
-- an agent became a fixed (provider, model) pair with no spare behind it.
--
-- An unrecognised rung (the blank string, which a third of these rows carry)
-- becomes ABSENT rather than mapped: capability is optional and defaults to
-- None, and a rung nobody wrote is not evidence for any rung. The capability
-- policy then answers "unresolved" for that pair, which is the true state.
--
-- content_hash is deliberately left alone. It is a dedupe key, not an
-- integrity check (get_by_content_hash is a lookup and no reader verifies it),
-- and it is computed in Python over the model dump, so SQL cannot recompute it
-- correctly. A stale hash costs one redundant version row the next time that
-- identity is saved; a hash rewritten to something wrong would cost more.

UPDATE agent_identity_versions
SET
    snapshot = JSONB_SET(
        (snapshot #- '{model,model_tier}') #- '{model,fallback_model}',
        '{model,capability}',
        TO_JSONB(
            CASE snapshot #>> '{model,model_tier}'
                WHEN 'large' THEN 'expert'
                WHEN 'medium' THEN 'capable'
                WHEN 'small' THEN 'basic'
                WHEN 'local-small' THEN 'basic'
            END
        )
    )
WHERE snapshot #>> '{model,model_tier}' IN ('large', 'medium', 'small', 'local-small');

-- Everything else: drop both keys and leave capability absent. An existence
-- test rather than a #>> comparison: fallback_model is JSON null on every one
-- of these rows, so #>> reads as SQL NULL and misses them. JSONB_EXISTS is the
-- function spelling of the ? operator; it is used here for parity with the
-- SQLite arm's JSON_TYPE reasoning above.
UPDATE agent_identity_versions
SET snapshot = (snapshot #- '{model,model_tier}') #- '{model,fallback_model}'
WHERE
    JSONB_EXISTS(snapshot #> '{model}', 'model_tier')
    OR JSONB_EXISTS(snapshot #> '{model}', 'fallback_model');

-- ── 3. The roster's second copy of the rung is dropped ────────
--
-- The rung is a property of an agent's bound (provider, model) pair, and it
-- already rides inside the agent's ``model`` object. A sibling copy sat beside
-- it at the top level, written once when the agent was matched and never
-- revised: the API projected THAT one, so the dashboard printed a rung the
-- routing gates did not use, in both directions (a re-graded catalogue or an
-- operator override moved the pair; a repointed binding moved the pair itself).
--
-- ``AgentConfig`` is ``extra="forbid"``, so with the field gone a stored roster
-- still carrying the key fails validation for every agent at once and the whole
-- company reads as empty behind a single warning. Hence this step.
--
-- The model-level rung is deliberately left alone: it is the roster's CLAIM,
-- which the catalogue's grade outranks wherever the catalogue has one, and it
-- is the only rung a pair the catalogue cannot grade has.

-- Two guards, because `settings` is one table holding every namespace's
-- values and only this row is JSON. Postgres does not promise to evaluate a
-- WHERE clause left to right, so the namespace/key equality alone does not
-- stop `value::JSONB` being attempted on the Fernet blob under
-- providers.configs or on a bare string like `log_only`; a CASE is the
-- documented construct that does. And `jsonb - text` raises on a scalar
-- operand, so the removal is applied per element rather than to whatever the
-- array happens to contain: one hand-edited non-object element beside one
-- well-formed agent would otherwise roll back this whole revision. The SQLite
-- arm gets both properties for free, since JSON_REMOVE returns a scalar
-- unchanged.
UPDATE settings
SET
    value = (
        SELECT
            JSONB_AGG(
                CASE
                    WHEN JSONB_TYPEOF(agent) = 'object' THEN agent - 'capability'
                    ELSE agent
                END
                ORDER BY ordinality
            )::TEXT
        -- The alias renames the set-returning function's own output columns,
        -- which is what makes `agent` and `ordinality` resolvable above; the
        -- linter reads `t` as unused because nothing qualifies with it.
        FROM JSONB_ARRAY_ELEMENTS(value::JSONB) WITH ORDINALITY AS t (agent, ordinality)  -- noqa: AL05
    )
WHERE
    namespace = 'company'
    AND key = 'agents'
    AND CASE
        WHEN namespace = 'company' AND key = 'agents'
            THEN
                JSONB_TYPEOF(value::JSONB) = 'array'
                AND EXISTS (
                    SELECT 1
                    FROM JSONB_ARRAY_ELEMENTS(value::JSONB) AS agent
                    WHERE
                        JSONB_TYPEOF(agent) = 'object'
                        AND JSONB_EXISTS(agent, 'capability')
                )
        ELSE FALSE
    END;

-- ── 4. Latching provider refusals become durable ──────────────
--
-- A latching failure (today only an empty balance) is honoured over a 24-hour
-- lookback rather than the 15-minute rate window, because a 402 that decayed
-- with the window would take the pair's agents out of service, stop the calls
-- that are its own evidence, and read clear one window later. The lookback is
-- meant to be the sole exit, and doubles as the retry-after.
--
-- It was not the sole exit. The outcomes it is read from are held in memory,
-- so a restart cleared every latch: an agent stood down under a verdict whose
-- own text says "this does not clear without an operator" was offered the same
-- work on the same refusing pair minutes later, and the operator who saw the
-- warning had no way to know it had ever been raised.
--
-- One row per (provider, model), replaced by each fresh refusal: the reader
-- honours the newest and nothing else, so a log would keep a row per refused
-- call to answer a question only its last entry decides. Everything else the
-- health tracker holds stays in memory on purpose; it is high-volume, it
-- decays within minutes, and losing it costs one fresh measurement.

CREATE TABLE provider_latched_failures (
    provider_name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(provider_name)) > 0),
    model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(model)) > 0),
    outcome_class TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(outcome_class)) > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    error_message TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(error_message)) > 0),
    response_time_ms DOUBLE PRECISION NOT NULL CHECK (response_time_ms >= 0),
    agent_id TEXT,
    task_id TEXT,
    PRIMARY KEY (provider_name, model)
);

CREATE INDEX idx_provider_latched_failures_occurred
ON provider_latched_failures (occurred_at DESC);

-- ── 5. Approval stops depending on the run it authorises ──────
--
-- The old constraint required task_id on every APPROVED charter, which forced
-- the approval to be written AFTER the dispatch it authorises. That left the
-- work pipeline unable to check the charter a brief names: the row is still
-- drafted at the moment the initiative stands up, so the only thing the spine
-- could verify was that some string had been supplied.
--
-- task_id is dispatch provenance, not approval provenance. So the coupling now
-- reads: the four decision columns are set iff the charter is APPROVED, and
-- only an APPROVED charter may name a run. An APPROVED charter with no run is
-- authorised work that has not been dispatched, which is a state the approve
-- path resumes rather than a state that should be unrepresentable.
--
-- No backfill: the old constraint guaranteed every existing APPROVED row
-- already carries a task_id, and the new one still admits those rows.

ALTER TABLE project_charters
DROP CONSTRAINT chk_charter_approval_coupling;

ALTER TABLE project_charters
ADD CONSTRAINT chk_charter_approval_coupling CHECK (
    (
        status = 'approved'
        AND approved_at IS NOT NULL AND approved_by IS NOT NULL
        AND forecast_id IS NOT NULL AND correlation_id IS NOT NULL
    )
    OR (
        status != 'approved'
        AND approved_at IS NULL AND approved_by IS NULL
        AND forecast_id IS NULL AND correlation_id IS NULL
        AND task_id IS NULL
    )
);
