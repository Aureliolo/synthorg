-- Four schema facts, one revision.
--
-- 1. The capability rename reaches the identity ARCHIVE, not just the roster.
-- 2. The roster's second copy of the capability rung is dropped.
-- 3. A latching provider refusal gets somewhere durable to live.
-- 4. A charter's approval stops depending on the run it authorises.
--
-- ── 1. The identity archive speaks the capability vocabulary ──
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

-- ── 2. The roster's second copy of the rung is dropped ────────
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
        -- Every branch of the CASE is load-bearing, because json_each hands
        -- back a SQL-typed value rather than the element's JSON text. Only an
        -- object or array arrives carrying its JSON subtype, so only those two
        -- survive reaggregation as themselves; a text element arrives as bare
        -- SQL text, which json_remove and json_type both reject as malformed
        -- JSON, and a boolean arrives as 1 or 0, which json_quote would rewrite
        -- into a number. One hand-edited row would otherwise take the whole
        -- revision down, which is the same failure the Postgres arm guards.
        --
        -- json() around the removal so each element aggregates back as an object
        -- rather than as a string holding an object's text. Ordered explicitly
        -- by the array index in a subquery rather than with an ORDER BY inside
        -- the aggregate: the in-aggregate form needs SQLite 3.44, and this
        -- revision runs against whatever libsqlite3 an operator's install
        -- happens to link, where a syntax error is an upgrade that stops dead.
        -- JSON() again on the way out of the subquery, because a value loses
        -- its JSON subtype crossing that boundary and JSON_GROUP_ARRAY would
        -- then aggregate every element as a STRING holding its own text.
        SELECT JSON_GROUP_ARRAY(JSON(element))
        FROM (
            SELECT
                CASE agent.type
                    WHEN 'object' THEN JSON(JSON_REMOVE(agent.value, '$.capability'))
                    WHEN 'array' THEN JSON(agent.value)
                    WHEN 'true' THEN JSON('true')
                    WHEN 'false' THEN JSON('false')
                    ELSE JSON_QUOTE(agent.value)
                END AS element
            FROM JSON_EACH(settings.value) AS agent
            ORDER BY agent.key
        ) AS ordered_agents
    )
WHERE
    namespace = 'company'
    AND key = 'agents'
    AND JSON_VALID(value)
    AND JSON_TYPE(value) = 'array'
    AND EXISTS (
        SELECT 1
        FROM JSON_EACH(settings.value) AS agent
        WHERE
            agent.type = 'object'
            AND JSON_TYPE(agent.value, '$.capability') IS NOT NULL
    );

-- ── 3. Latching provider refusals become durable ──────────────
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

-- ── 4. Approval stops depending on the run it authorises ──────
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
-- SQLite cannot alter a CHECK in place, so the table is rebuilt into its final
-- shape and its five indices recreated. No backfill: the old constraint
-- guaranteed every existing APPROVED row already carries a task_id, and the
-- new one still admits those rows.

CREATE TABLE project_charters_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL CHECK (LENGTH(TRIM(conversation_id)) > 0),
    created_by TEXT NOT NULL CHECK (LENGTH(TRIM(created_by)) > 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status TEXT NOT NULL DEFAULT 'drafted' CHECK (
        status IN ('drafted', 'approved', 'cancelled')
    ),
    title TEXT NOT NULL CHECK (LENGTH(TRIM(title)) > 0),
    brief TEXT NOT NULL CHECK (LENGTH(TRIM(brief)) > 0),
    goals TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(goals) AND JSON_TYPE(goals) = 'array'),
    constraints TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(constraints) AND JSON_TYPE(constraints) = 'array'),
    success_criteria TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(success_criteria) AND JSON_TYPE(success_criteria) = 'array'),
    in_scope TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(in_scope) AND JSON_TYPE(in_scope) = 'array'),
    out_of_scope TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(out_of_scope) AND JSON_TYPE(out_of_scope) = 'array'),
    envelope_amount REAL NOT NULL CHECK (envelope_amount > 0),
    envelope_currency TEXT NOT NULL CHECK (LENGTH(envelope_currency) = 3),
    envelope_deadline TEXT
    CHECK (
        envelope_deadline IS NULL
        OR envelope_deadline LIKE '%+00:00'
        OR envelope_deadline LIKE '%Z'
    ),
    envelope_time_horizon TEXT
    CHECK (
        envelope_time_horizon IS NULL
        OR LENGTH(TRIM(envelope_time_horizon)) > 0
    ),
    project_id TEXT CHECK (project_id IS NULL OR LENGTH(TRIM(project_id)) > 0),
    proposed_project_name TEXT
    CHECK (
        proposed_project_name IS NULL
        OR LENGTH(TRIM(proposed_project_name)) > 0
    ),
    proposed_project_description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    ),
    approved_at TEXT
    CHECK (
        approved_at IS NULL
        OR approved_at LIKE '%+00:00'
        OR approved_at LIKE '%Z'
    ),
    approved_by TEXT
    CHECK (approved_by IS NULL OR LENGTH(TRIM(approved_by)) > 0),
    forecast_id TEXT
    CHECK (forecast_id IS NULL OR LENGTH(TRIM(forecast_id)) > 0),
    correlation_id TEXT
    CHECK (correlation_id IS NULL OR LENGTH(TRIM(correlation_id)) > 0),
    task_id TEXT CHECK (task_id IS NULL OR LENGTH(TRIM(task_id)) > 0),
    CONSTRAINT chk_charter_project_binding CHECK (
        (project_id IS NOT NULL AND proposed_project_name IS NULL)
        OR (project_id IS NULL AND proposed_project_name IS NOT NULL)
    ),
    CONSTRAINT chk_charter_approval_coupling CHECK (
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
    )
);

INSERT INTO project_charters_new (
    id,
    conversation_id,
    created_by,
    version,
    status,
    title,
    brief,
    goals,
    constraints,
    success_criteria,
    in_scope,
    out_of_scope,
    envelope_amount,
    envelope_currency,
    envelope_deadline,
    envelope_time_horizon,
    project_id,
    proposed_project_name,
    proposed_project_description,
    created_at,
    updated_at,
    approved_at,
    approved_by,
    forecast_id,
    correlation_id,
    task_id
)
SELECT
    id,
    conversation_id,
    created_by,
    version,
    status,
    title,
    brief,
    goals,
    constraints,
    success_criteria,
    in_scope,
    out_of_scope,
    envelope_amount,
    envelope_currency,
    envelope_deadline,
    envelope_time_horizon,
    project_id,
    proposed_project_name,
    proposed_project_description,
    created_at,
    updated_at,
    approved_at,
    approved_by,
    forecast_id,
    correlation_id,
    task_id
FROM project_charters;

DROP TABLE project_charters;
ALTER TABLE project_charters_new RENAME TO project_charters;

CREATE INDEX idx_project_charters_status ON project_charters (status);
CREATE INDEX idx_project_charters_project_id ON project_charters (project_id);
CREATE INDEX idx_project_charters_created_by ON project_charters (created_by);
CREATE INDEX idx_project_charters_conversation_id
ON project_charters (conversation_id);
CREATE INDEX idx_project_charters_created_id
ON project_charters (created_at DESC, id DESC);
