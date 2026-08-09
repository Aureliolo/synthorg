-- Two things a record could not say, and one it should never have to.
--
-- 1. The approvals table refused the one source the plan-review gate writes.
--
-- ``ApprovalSource`` has five members; ``approvals_source_check`` admitted
-- four. ``plan_review`` was never in the list, so every plan reaching human
-- review failed to persist its approval. The gap was invisible for as long
-- as it existed because no approval reached the database at all: the store
-- was constructed without a repository, held its queue in memory, and the
-- CHECK was never evaluated. The first run with durable approvals hit it
-- immediately, and the plan was failed nine milliseconds after reaching
-- PENDING_REVIEW, with no approval for an operator to decide.
--
-- Widening rather than dropping: the list is the schema's statement of what
-- the column may hold, and the fix is to make that statement true, not to
-- stop making it.
--
-- 2. A task that had ever spent money could never be deleted.
--
-- Spend, metrics, approvals and decision records all name the task they are
-- about, and each pinned it with a foreign key. That made every one of them
-- a veto: a live run could not delete a project because one of its tasks
-- had recorded a cost, and the refusal surfaced as
-- ``cost_records_task_id_fkey`` rather than as a reason. The pins go, and
-- the identifier stays exactly as written, matching
-- ``cost_records.project_id`` which has never carried one for the same
-- reason: a record of something that really happened must not be able to
-- refuse the removal of what it happened to.
--
-- ``plans.parent_task_id`` keeps its RESTRICT. It is not history about a
-- task, it is a live plan built from one, and the teardown already removes
-- plans before tasks so it never fires.
--
-- 3. So a retained identifier still resolves.
--
-- ``deleted_entities`` records what a deleted task, plan or project was,
-- who removed it and when. Only a person deletes an entity, so only a
-- person's action writes here, and ``deleted_by`` is NOT NULL to keep it
-- that way.
--
-- Runs in yoyo's default transaction. Re-adding the CHECK requires a full
-- table scan to validate existing rows; every stored value is one of the
-- four already admitted, so the scan cannot fail, and dropping a foreign
-- key takes only a brief lock on tables this small.

ALTER TABLE approvals DROP CONSTRAINT approvals_source_check;

ALTER TABLE approvals ADD CONSTRAINT approvals_source_check CHECK (
    source IN (
        'parked_context', 'review_gate',
        'conversational_intake', 'conversational_invite',
        'plan_review'
    )
);

ALTER TABLE cost_records DROP CONSTRAINT cost_records_task_id_fkey;
ALTER TABLE task_metrics DROP CONSTRAINT task_metrics_task_id_fkey;
ALTER TABLE decision_records DROP CONSTRAINT decision_records_task_id_fkey;
ALTER TABLE approvals DROP CONSTRAINT approvals_task_id_fkey;

CREATE TABLE deleted_entities (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    entity_kind TEXT NOT NULL
    CHECK (entity_kind IN ('task', 'plan', 'project')),
    entity_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(entity_id)) > 0),
    display_name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(display_name)) > 0),
    deleted_by TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(deleted_by)) > 0),
    deleted_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_deleted_entities_lookup
ON deleted_entities (entity_id, entity_kind, deleted_at DESC);
