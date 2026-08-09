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
-- A UNIQUE INDEX rather than a table rebuild: the index enforces the
-- invariant on every row the app writes from now on, which is every row
-- that matters for dedup. A row predating the write path carries no claim,
-- and a NULL never matches under a unique index, so it could never be
-- deduped against a redelivery of itself. The backfill gives each one a
-- per-row synthetic key instead, which cannot collide with a UUID4 claim.
--
-- ``timestamp`` rides in the key because the Postgres twin of this table is
-- a TimescaleDB hypertable, and a unique index on a hypertable must include
-- the partitioning column. Both backends therefore carry the same key, and
-- it still catches the duplicate that actually happens: a redelivery
-- re-sends the same immutable record, so the same claim AND the same
-- timestamp.
--
-- ``project_id`` deliberately carries no foreign key, matching
-- ``project_cost_aggregates.project_id``, which the same ``record()`` call
-- writes in the same transaction. A cost row is financial evidence of a
-- call that really happened: refusing the insert because the project row is
-- missing (or gone) would lose the spend rather than protect it, and would
-- also split the two stores, leaving the aggregate counting money the
-- record table dropped.
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

-- No-op on every install shipped so far: nothing wrote this table before
-- this revision, which is the defect it exists to close. Kept because
-- "empty" is a claim about today, and it costs nothing on an empty table.
UPDATE cost_records
SET claim_id = 'legacy:' || rowid
WHERE claim_id IS NULL;

CREATE UNIQUE INDEX idx_cost_records_claim_id
ON cost_records (claim_id, timestamp);

CREATE INDEX idx_cost_records_project_timestamp
ON cost_records (project_id, timestamp DESC);

-- Blank is the state both columns exist to distinguish from absent (no
-- fallback stood in; the panel did produce a verdict), so the row rejects it
-- the way the model's ``NotBlankStr | None`` does.
ALTER TABLE plans ADD COLUMN planning_strategy TEXT
CHECK (planning_strategy IS NULL OR LENGTH(TRIM(planning_strategy)) > 0);

ALTER TABLE plans ADD COLUMN review_absent_reason TEXT
CHECK (
    review_absent_reason IS NULL
    OR LENGTH(TRIM(review_absent_reason)) > 0
);

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

-- The read is always "this entity's transitions, newest first", and the
-- tie-break on id is part of that ordering, so both sort keys ride in the
-- index and the query never needs a sort step.
CREATE INDEX idx_lifecycle_transitions_entity
ON lifecycle_transitions (entity_kind, entity_id, occurred_at DESC, id DESC);
