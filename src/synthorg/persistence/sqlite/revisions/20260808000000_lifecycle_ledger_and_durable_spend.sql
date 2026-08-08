-- Spend becomes durable, status changes become a record, and a plan says
-- which planner produced it and why it carries no review.
--
-- ``cost_records`` had no writer anywhere in the product: the tracker
-- appended to memory and incremented ``project_cost_aggregates``, so every
-- restart lost the window a ceiling is enforced over and every deliverable
-- receipt reported zero. The table gains the two columns the record already
-- carries and could not persist: ``claim_id`` (the tracker's idempotency
-- key, so the durable append is idempotent for a record with no project,
-- which the aggregate path skips dedup for entirely) and ``project_id``.
--
-- A UNIQUE INDEX rather than a table rebuild: ``cost_records`` is the
-- highest-volume table here, and the index enforces the invariant on every
-- row the app writes from now on, which is every row that matters for
-- dedup. Existing rows are backfilled with a per-row synthetic key so the
-- index can be created at all; they predate the write path and can never
-- collide with a UUID4 claim.
--
-- ``timestamp`` rides in the key because the Postgres twin of this table is
-- a TimescaleDB hypertable, and a unique index on a hypertable must include
-- the partitioning column. Both backends therefore carry the same key, and
-- it still catches the duplicate that actually happens: a redelivery
-- carries the same record, so the same claim AND the same timestamp.
--
-- ``lifecycle_transitions`` is new: a plan reaching COMPLETED had no durable
-- actor record, so "only ``evaluate.py`` writes COMPLETED" was provable from
-- a container log and nowhere else.
--
-- ``plans`` gains ``planning_strategy`` (which planner produced the items,
-- set when a fallback stood in) and ``review_absent_reason`` (why a seated
-- panel produced no review). Both are nullable: the common case is the
-- configured planner and a real review, and neither has anything to say.

ALTER TABLE cost_records ADD COLUMN claim_id TEXT;

ALTER TABLE cost_records ADD COLUMN project_id TEXT;

UPDATE cost_records
SET claim_id = 'legacy:' || rowid
WHERE claim_id IS NULL;

CREATE UNIQUE INDEX idx_cost_records_claim_id
ON cost_records (claim_id, timestamp);

CREATE INDEX idx_cost_records_project_timestamp
ON cost_records (project_id, timestamp DESC);

ALTER TABLE plans ADD COLUMN planning_strategy TEXT;

ALTER TABLE plans ADD COLUMN review_absent_reason TEXT;

CREATE TABLE lifecycle_transitions (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    -- Two kinds share one ledger because they are one question: what moved
    -- this initiative, and who moved it. Splitting them would make the
    -- answer a join.
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('plan', 'project')),
    entity_id TEXT NOT NULL CHECK (LENGTH(TRIM(entity_id)) > 0),
    -- Null only for the first observed status of an entity.
    from_status TEXT CHECK (from_status IS NULL OR LENGTH(TRIM(from_status)) > 0),
    to_status TEXT NOT NULL CHECK (LENGTH(TRIM(to_status)) > 0),
    -- Who asked. Null means the system moved it on its own schedule (a
    -- reconciler pass, a rollup), which is itself the answer to "who".
    requested_by TEXT CHECK (requested_by IS NULL OR LENGTH(TRIM(requested_by)) > 0),
    reason TEXT CHECK (reason IS NULL OR LENGTH(TRIM(reason)) > 0),
    entity_version INTEGER NOT NULL CHECK (entity_version >= 0),
    occurred_at TEXT NOT NULL
);

-- The read is always "every transition of this entity, oldest first", so
-- the index carries the whole query.
CREATE INDEX idx_lifecycle_transitions_entity
ON lifecycle_transitions (entity_kind, entity_id, occurred_at);
