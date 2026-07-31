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
--
-- This scans cost_records once. A LIKE 'prefix%' predicate cannot use a
-- plain B-tree index under a non-C collation, and adding a text_pattern_ops
-- index to serve one statement would cost a full build of its own on the
-- highest-insert-rate table here, then be dropped. The scan is accepted: it
-- runs once, it holds no ACCESS EXCLUSIVE lock (the statement-per-commit
-- note above), and concurrent writers are unblocked throughout.
UPDATE cost_records SET agent_id = NULL
WHERE agent_id LIKE 'system%';

-- Existing connection rows take the column defaults, which is the honest
-- reading of a verdict recorded before these columns existed: no detail was
-- captured, no latency was measured, and nothing is claimed about ingest.
ALTER TABLE connections ADD COLUMN health_detail TEXT;

-- The upper bound rejects both Infinity and NaN: Postgres orders NaN above
-- every number, so `>= 0` admits it while `< 'Infinity'` does not. It has to
-- be rejected here because ConnectionHealth sets allow_inf_nan=False, so a
-- non-finite value that reached the column would make the row unreadable.
ALTER TABLE connections ADD COLUMN health_latency_ms DOUBLE PRECISION
CHECK (
    health_latency_ms IS NULL
    OR (
        health_latency_ms >= 0
        AND health_latency_ms < 'Infinity'::DOUBLE PRECISION
    )
);

ALTER TABLE connections ADD COLUMN health_webhook_ingest TEXT
NOT NULL DEFAULT 'not_applicable'
CHECK (
    health_webhook_ingest IN ('not_applicable', 'ready', 'unconfigured')
);

-- A provider that refused with a rate limit already said how long to wait.
-- Storing it lets the recheck interval honour that answer instead of probing
-- again on our own schedule, which cannot succeed and spends a request to be
-- refused a second time.
-- Finite for the same reason, and one more: this value is a floor on the
-- recheck interval, so an infinite one would retire the connection from
-- probing for good on the say-so of the endpoint that refused it.
ALTER TABLE connections ADD COLUMN health_retry_after_seconds DOUBLE PRECISION
CHECK (
    health_retry_after_seconds IS NULL
    OR (
        health_retry_after_seconds > 0
        AND health_retry_after_seconds < 'Infinity'::DOUBLE PRECISION
    )
);
