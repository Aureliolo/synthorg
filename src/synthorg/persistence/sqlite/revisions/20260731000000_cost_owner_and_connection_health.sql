-- Two related corrections to what the system is willing to record.
--
-- 1. Cost attribution stops inventing entity ids for work that has no entity.
--
-- agent_id and task_id are references to real rows, and task_id is an actual
-- foreign key into tasks. Subsystem work (memory embedding, reranking,
-- consolidation, chief-of-staff chat, code modification, safety
-- classification) belongs to no agent and no task, so every one of those call
-- sites used to write a synthetic id such as 'system:memory:embedding'. That
-- id matches no task row, so the insert failed the constraint and the record
-- was dropped: the spend of every subsystem LLM call went unrecorded and the
-- budget under-reported by exactly that amount.
--
-- Dropping NOT NULL lets those calls record honestly with no owner, which the
-- foreign key accepts. What the call was for is not lost: it is carried by
-- prompt_class_id for a call that wraps a system prompt, and by call_category
-- for one that does not (an embedding call has no prompt).
--
-- 2. Connection health persists the whole verdict, not just its headline.
--
-- The aggregate-health endpoint used to run a live probe for every connection
-- on every request, and the dashboard polls it while the Connections view is
-- open, so opening that page re-probed the entire catalog on a loop. A probe
-- is not free: a connection pointed at a metered third-party API bills per
-- call. Serving the stored verdict instead needs that verdict to be complete,
-- or the cached answer would be thinner than a live one: a failure with no
-- reason, and a webhook state of "claims nothing" for a connection that does
-- have an inbound path.
--
-- On the rebuild below: SQLite cannot drop a column constraint in place, so
-- cost_records is recreated and copied. Legacy synthetic ids are normalised to
-- NULL on the way across, because they reference no task and carrying them
-- over would re-break the foreign key the rebuild re-establishes.
--
-- There is deliberately no PRAGMA foreign_keys bracket around the rebuild.
-- yoyo runs a revision inside a transaction, and that pragma is a documented
-- no-op while one is open, so the bracket earlier drafts carried was inert and
-- said otherwise. It is also unnecessary: yoyo's own connection defaults to
-- foreign_keys=OFF (the same reason three earlier revisions in this directory
-- carry no bracket), and after normalisation every carried value is either a
-- real task id or NULL, both of which satisfy the constraint once the
-- application reconnects with enforcement on. Integrity is verified after the
-- fact by scripts/check_schema_drift_revisions.py, which runs
-- PRAGMA foreign_key_check and reads its result, rather than by a statement
-- here whose result nothing inspects.
CREATE TABLE cost_records_new (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT,
    task_id TEXT REFERENCES tasks (id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    timestamp TEXT NOT NULL,
    call_category TEXT,
    prompt_class_id TEXT
);

INSERT INTO cost_records_new (
    rowid, agent_id, task_id, provider, model, input_tokens,
    output_tokens, cost, currency, timestamp, call_category, prompt_class_id
)
SELECT
    rowid,
    CASE WHEN agent_id LIKE 'system%' THEN NULL ELSE agent_id END AS agent_id,
    CASE WHEN task_id LIKE 'system:%' THEN NULL ELSE task_id END AS task_id,
    provider,
    model,
    input_tokens,
    output_tokens,
    cost,
    currency,
    timestamp,
    call_category,
    prompt_class_id
FROM cost_records;

DROP TABLE cost_records;
ALTER TABLE cost_records_new RENAME TO cost_records;

CREATE INDEX idx_cost_records_agent_id ON cost_records (agent_id);
CREATE INDEX idx_cost_records_task_id ON cost_records (task_id);
CREATE INDEX idx_cost_records_timestamp ON cost_records (timestamp DESC);
CREATE INDEX idx_cost_records_agent_timestamp
ON cost_records (agent_id, timestamp DESC);
CREATE INDEX idx_cost_records_task_timestamp
ON cost_records (task_id, timestamp DESC);
CREATE INDEX idx_cost_records_prompt_class_timestamp
ON cost_records (prompt_class_id, timestamp DESC);

-- Existing connection rows take the column defaults, which is the honest
-- reading of a verdict recorded before these columns existed: no detail was
-- captured, no latency was measured, and nothing is claimed about ingest.
ALTER TABLE connections ADD COLUMN health_detail TEXT;

ALTER TABLE connections ADD COLUMN health_latency_ms REAL
CHECK (health_latency_ms IS NULL OR health_latency_ms >= 0);

ALTER TABLE connections ADD COLUMN health_webhook_ingest TEXT
NOT NULL DEFAULT 'not_applicable'
CHECK (
    health_webhook_ingest IN ('not_applicable', 'ready', 'unconfigured')
);

-- A provider that refused with a rate limit already said how long to wait.
-- Storing it lets the recheck interval honour that answer instead of probing
-- again on our own schedule, which cannot succeed and spends a request to be
-- refused a second time.
ALTER TABLE connections ADD COLUMN health_retry_after_seconds REAL
CHECK (health_retry_after_seconds IS NULL OR health_retry_after_seconds > 0);
