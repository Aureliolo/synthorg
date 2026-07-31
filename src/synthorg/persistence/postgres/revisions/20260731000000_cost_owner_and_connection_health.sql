-- transactional: false
--
-- Two related corrections to what the system is willing to record.
--
-- 1. Cost attribution stops inventing entity ids for work that has no entity.
--
-- agent_id and task_id are references to real rows, and task_id is an actual
-- foreign key into tasks. Subsystem work (memory embedding, reranking,
-- consolidation, chief-of-staff chat, code modification, safety
-- classification) belongs to no agent and no task, so every one of those call
-- sites used to write a synthetic id such as 'system:memory:embedding'. That
-- id matches no task row, so the insert failed the foreign key and the record
-- was dropped: the spend of every subsystem LLM call went unrecorded and the
-- budget under-reported by exactly that amount.
--
-- Dropping NOT NULL lets those calls record honestly with no owner, which the
-- foreign key accepts. What the call was for is not lost: it is carried by
-- prompt_class_id for a call that wraps a system prompt, and by call_category
-- for one that does not (an embedding call has no prompt).
--
-- 2. Connection health persists the whole verdict, not just its headline, so
-- the aggregate-health endpoint can serve a stored answer instead of probing
-- every connection on every poll. A probe against a metered third-party API
-- bills per call, and a cached verdict missing its reason would be a thinner
-- answer than the live one it replaces.
--
-- This revision runs WITHOUT a wrapping transaction, which is load-bearing
-- rather than incidental. ALTER TABLE ... DROP NOT NULL takes ACCESS
-- EXCLUSIVE on cost_records, the highest-insert-rate table in the system.
-- Held across the backfill below it would block every reader and writer for
-- the duration of a sequential scan, and the cost recorder abandons a write
-- after a few seconds, so the migration would drop exactly the records it
-- exists to preserve. Statement-per-commit releases each lock immediately.
ALTER TABLE cost_records ALTER COLUMN agent_id DROP NOT NULL;

ALTER TABLE cost_records ALTER COLUMN task_id DROP NOT NULL;

-- Normalise any legacy synthetic owner to NULL, so 'system' stops appearing
-- in the dashboard as though it were an agent. Only agent_id needs this: a
-- synthetic task_id could never have been committed, because the foreign key
-- rejected it, so a scan for one would be work with no possible result.
UPDATE cost_records SET agent_id = NULL
WHERE agent_id LIKE 'system%';

-- Existing connection rows take the column defaults, which is the honest
-- reading of a verdict recorded before these columns existed: no detail was
-- captured, no latency was measured, and nothing is claimed about ingest.
ALTER TABLE connections ADD COLUMN health_detail TEXT;

ALTER TABLE connections ADD COLUMN health_latency_ms DOUBLE PRECISION
CHECK (health_latency_ms IS NULL OR health_latency_ms >= 0);

ALTER TABLE connections ADD COLUMN health_webhook_ingest TEXT
NOT NULL DEFAULT 'not_applicable'
CHECK (
    health_webhook_ingest IN ('not_applicable', 'ready', 'unconfigured')
);
