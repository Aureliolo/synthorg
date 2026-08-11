-- Two things a spend ceiling could not know.
--
-- 1. Whether the money it counts measures anything.
--
-- A provider that bills by flat subscription records cost 0.0 on every
-- call. That is the correct number: there is no per-1k price to attribute.
-- What it is not is headroom, and every reader treated it as headroom
-- because nothing on the row said which of the two zeroes it was. The
-- budget page reported a full month remaining forever, every deliverable
-- receipt reported nothing spent, and the hiring signal read an
-- unmeasurable window as safe to hire against.
--
-- ``billing_model`` is stamped from the connection's own declaration at
-- ingestion, and carried on the row for the same reason ``currency`` is: a
-- connection that later changes contract must not rewrite the history of
-- what was measurable, and a connection since deleted must still be
-- answerable. ``unknown`` is the honest default for rows written before
-- anyone declared, and it reads as unmeasurable rather than as per-token:
-- assuming a ceiling binds when it may not is the failure being fixed.
--
-- 2. Anything at all, against such a provider.
--
-- The money ceiling is the only in-loop backstop a run has, and it cannot
-- fire when cost never rises. Tokens are measured on every provider,
-- billed or not, so ``tasks.hard_token_ceiling`` is the same ceiling in the
-- unit that is always available. NULL falls back to the global
-- ``budget.run_hard_token_ceiling`` setting, matching ``hard_ceiling``.
--
-- SQLite cannot alter a CHECK, so ``cost_records`` is rebuilt (create-new,
-- copy, drop, rename) and its indexes recreated. Nothing references it by
-- foreign key, so the drop fires no cascades. ``tasks`` only gains a
-- nullable column, which ADD COLUMN handles in place.

CREATE TABLE cost_records_new (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT,
    task_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    timestamp TEXT NOT NULL,
    call_category TEXT,
    prompt_class_id TEXT,
    claim_id TEXT,
    project_id TEXT,
    billing_model TEXT NOT NULL DEFAULT 'unknown' CHECK (
        billing_model IN ('per_token', 'flat_rate', 'unknown')
    )
);

INSERT INTO cost_records_new (
    rowid, agent_id, task_id, provider, model, input_tokens, output_tokens,
    cost, currency, timestamp, call_category, prompt_class_id, claim_id,
    project_id
)
SELECT
    rowid,
    agent_id,
    task_id,
    provider,
    model,
    input_tokens,
    output_tokens,
    cost,
    currency,
    timestamp,
    call_category,
    prompt_class_id,
    claim_id,
    project_id
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
CREATE UNIQUE INDEX idx_cost_records_claim_id
ON cost_records (claim_id, timestamp);
CREATE INDEX idx_cost_records_project_timestamp
ON cost_records (project_id, timestamp DESC);

ALTER TABLE tasks ADD COLUMN hard_token_ceiling INTEGER;
