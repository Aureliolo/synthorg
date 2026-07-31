-- Cost attribution stops inventing entity ids for work that has no entity.
--
-- agent_id and task_id are references to real rows, and task_id is an actual
-- foreign key into tasks. Subsystem work (memory embedding, reranking,
-- consolidation, chief-of-staff chat, code modification, safety
-- classification) belongs to no agent and no task, so every one of those call
-- sites used to write a synthetic id such as 'system:memory:embedding'. That
-- id matches no task row, so with PRAGMA foreign_keys=ON the insert failed the
-- constraint and the record was dropped: the spend of every subsystem LLM call
-- went unrecorded and the budget under-reported by exactly that amount.
--
-- Dropping NOT NULL lets those calls record honestly with no owner, which the
-- foreign key accepts. What the call was for is not lost: prompt_class_id
-- already carries its PromptPurposeId, and the cost-attribution-purpose gate
-- guarantees every LLM chokepoint supplies one.
--
-- SQLite cannot drop a column constraint in place, so this is the documented
-- 12-step table rebuild. Legacy synthetic ids are normalised to NULL on the
-- way across: they reference no task, so carrying them over would re-break the
-- foreign key the rebuild re-establishes.
PRAGMA foreign_keys = OFF;

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

PRAGMA foreign_key_check;

PRAGMA foreign_keys = ON;
