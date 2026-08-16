-- Four schema facts, one revision.
--
-- 1. The project's human name is denormalised onto the plan.
-- 2. The capability rename reaches the identity ARCHIVE, not just the roster.
-- 3. The roster's second copy of the capability rung is dropped.
-- 4. A latching provider refusal gets somewhere durable to live.
--
-- ── 1. plans.project_name ─────────────────────────────────────
--
-- Every plan surface (the review inbox row, the detail header, the chat card
-- that links to it) had only plans.project, which is a project id. An id is a
-- database key, not information: it is not memorable, not comparable by eye,
-- and it crowds out the name it stands in for. This is the same denormalisation
-- objective_title already carries, for the same stated reason -- so the surface
-- never has to resolve an id, and never falls back to showing one.
--
-- SQLite cannot add a NOT NULL column carrying a CHECK and then drop the
-- transient default, so the table is rebuilt into its final shape, backfilled
-- from projects, and its five indices recreated.

CREATE TABLE plans_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (LENGTH(TRIM(project)) > 0),
    project_name TEXT NOT NULL CHECK (LENGTH(TRIM(project_name)) > 0),
    objective_id TEXT NOT NULL CHECK (LENGTH(TRIM(objective_id)) > 0),
    objective_title TEXT NOT NULL CHECK (LENGTH(TRIM(objective_title)) > 0),
    parent_task_id TEXT NOT NULL
    REFERENCES tasks (id) ON DELETE RESTRICT
    CHECK (LENGTH(TRIM(parent_task_id)) > 0),
    items TEXT NOT NULL
    CHECK (
        JSON_VALID(items) AND JSON_TYPE(items) = 'array'
        AND (status IN ('planning', 'failed') OR JSON_ARRAY_LENGTH(items) > 0)
    ),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN (
        'planning', 'draft', 'pending_review', 'approved', 'executing',
        'integrating', 'evaluating', 'completed', 'rejected', 'superseded',
        'failed'
    )),
    failure_reason TEXT CHECK (failure_reason IS NULL OR LENGTH(TRIM(failure_reason)) > 0),
    forecast_id TEXT,
    review TEXT,
    open_questions TEXT NOT NULL DEFAULT '[]',
    assumptions TEXT NOT NULL DEFAULT '[]',
    objective_criteria TEXT NOT NULL DEFAULT '[]',
    version_history TEXT NOT NULL DEFAULT '[]',
    replan_generation INTEGER NOT NULL DEFAULT 0 CHECK (replan_generation >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    planning_strategy TEXT
    CHECK (planning_strategy IS NULL OR LENGTH(TRIM(planning_strategy)) > 0),
    review_absent_reason TEXT
    CHECK (
        review_absent_reason IS NULL
        OR LENGTH(TRIM(review_absent_reason)) > 0
    ),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);

-- COALESCE, not a join that drops rows: a plan whose project is gone keeps its
-- id as the name, which is the honest answer (there is no name to recover) and
-- keeps the migration total rather than silently losing the plan.
INSERT INTO plans_new (
    id, project, project_name, objective_id, objective_title, parent_task_id,
    items, task_structure, coordination_topology, status, failure_reason,
    forecast_id, review, open_questions, assumptions, objective_criteria,
    version_history, replan_generation, version, created_at, updated_at,
    planning_strategy, review_absent_reason
)
SELECT
    plans.id,
    plans.project,
    COALESCE(
        (
            SELECT projects.name FROM projects
            WHERE projects.id = plans.project
        ),
        plans.project
    ) AS project_name,
    plans.objective_id,
    plans.objective_title,
    plans.parent_task_id,
    plans.items,
    plans.task_structure,
    plans.coordination_topology,
    plans.status,
    plans.failure_reason,
    plans.forecast_id,
    plans.review,
    plans.open_questions,
    plans.assumptions,
    plans.objective_criteria,
    plans.version_history,
    plans.replan_generation,
    plans.version,
    plans.created_at,
    plans.updated_at,
    plans.planning_strategy,
    plans.review_absent_reason
FROM plans;

DROP TABLE plans;
ALTER TABLE plans_new RENAME TO plans;

CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
CREATE INDEX idx_plans_project_status ON plans (project, status, id);
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);

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
    snapshot = JSON_SET(
        JSON_REMOVE(snapshot, '$.model.model_tier', '$.model.fallback_model'),
        '$.model.capability',
        CASE JSON_EXTRACT(snapshot, '$.model.model_tier')
            WHEN 'large' THEN 'expert'
            WHEN 'medium' THEN 'capable'
            WHEN 'small' THEN 'basic'
            WHEN 'local-small' THEN 'basic'
        END
    )
WHERE
    JSON_EXTRACT(snapshot, '$.model.model_tier')
    IN ('large', 'medium', 'small', 'local-small');

-- Everything else: drop both keys and leave capability absent. json_type is
-- the existence test that survives a JSON null, which is what fallback_model
-- holds on every one of these rows; json_extract returns SQL NULL for both a
-- missing key and a null value, so it cannot tell them apart.
UPDATE agent_identity_versions
SET snapshot = JSON_REMOVE(snapshot, '$.model.model_tier', '$.model.fallback_model')
WHERE
    JSON_TYPE(snapshot, '$.model.model_tier') IS NOT NULL
    OR JSON_TYPE(snapshot, '$.model.fallback_model') IS NOT NULL;

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

UPDATE settings
SET
    value = (
        -- json() around the removal so each element aggregates back as an object
        -- rather than as a string holding an object's text.
        SELECT JSON_GROUP_ARRAY(JSON(JSON_REMOVE(agent.value, '$.capability')))
        FROM JSON_EACH(settings.value) AS agent
    )
WHERE
    namespace = 'company'
    AND key = 'agents'
    AND JSON_VALID(value)
    AND JSON_TYPE(value) = 'array'
    AND EXISTS (
        SELECT 1
        FROM JSON_EACH(settings.value) AS agent
        WHERE JSON_TYPE(agent.value, '$.capability') IS NOT NULL
    );

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
    provider_name TEXT NOT NULL CHECK (LENGTH(TRIM(provider_name)) > 0),
    model TEXT NOT NULL CHECK (LENGTH(TRIM(model)) > 0),
    outcome_class TEXT NOT NULL CHECK (LENGTH(TRIM(outcome_class)) > 0),
    occurred_at TEXT NOT NULL CHECK (
        occurred_at LIKE '%+00:00' OR occurred_at LIKE '%Z'
    ),
    error_message TEXT NOT NULL CHECK (LENGTH(TRIM(error_message)) > 0),
    response_time_ms REAL NOT NULL CHECK (response_time_ms >= 0),
    agent_id TEXT,
    task_id TEXT,
    PRIMARY KEY (provider_name, model)
);

CREATE INDEX idx_provider_latched_failures_occurred
ON provider_latched_failures (occurred_at DESC);
