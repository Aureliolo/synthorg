-- Company-grade plan review: extend the durable Plan with the review-surface
-- fields and add a per-item comment thread.
--
-- objective_title denormalises the objective's human title onto the plan so the
-- review surface never resolves (or falls back to) a raw id. review holds the
-- consolidated stakeholder-panel review (JSONB, null until reviewed).
-- open_questions / assumptions are JSONB string arrays the owner surfaces for the
-- human. objective_criteria denormalises the objective's acceptance criteria onto
-- the plan so the coverage map can flag any criterion no item advances, without
-- resolving the parent task. version_history is a JSONB array of prior-version
-- snapshots, for diffing. plan_item_comments is an async discussion thread keyed
-- by (plan_id, item_id), written independently of the version-guarded plan row so
-- a comment never conflicts with a plan rework.

ALTER TABLE plans
ADD COLUMN objective_title TEXT NOT NULL DEFAULT '',
ADD COLUMN review JSONB,
ADD COLUMN open_questions JSONB NOT NULL DEFAULT '[]'::JSONB,
ADD COLUMN assumptions JSONB NOT NULL DEFAULT '[]'::JSONB,
ADD COLUMN objective_criteria JSONB NOT NULL DEFAULT '[]'::JSONB,
ADD COLUMN version_history JSONB NOT NULL DEFAULT '[]'::JSONB;

-- Backfill any pre-existing plan's title from its objective id (no human title
-- was captured before this migration); new plans always carry a real title.
UPDATE plans SET objective_title = objective_id
WHERE objective_title = '';

-- objective_title carries the same non-blank guard as its sibling id columns.
-- The transient '' default (needed to add the NOT NULL column to existing rows)
-- is dropped now the backfill guarantees every row is non-blank.
ALTER TABLE plans ALTER COLUMN objective_title DROP DEFAULT;
-- Add the constraint NOT VALID, then validate separately: the backfill above
-- already guarantees non-blank values, so a full-table validating scan under
-- the ALTER's lock is avoidable. VALIDATE takes only a SHARE UPDATE EXCLUSIVE
-- lock, so concurrent reads/writes on a hot plans table are not blocked.
ALTER TABLE plans
ADD CONSTRAINT plans_objective_title_check
CHECK (CHAR_LENGTH(TRIM(objective_title)) > 0) NOT VALID;
ALTER TABLE plans VALIDATE CONSTRAINT plans_objective_title_check;

CREATE TABLE plan_item_comments (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    plan_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(plan_id)) > 0),
    item_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(item_id)) > 0),
    author TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(author)) > 0),
    body TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(body)) > 0),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_plan_item_comments_plan_item
ON plan_item_comments (plan_id, item_id, created_at);

-- Optimistic concurrency for the Project aggregate: a version column so a
-- staffing write (stamping a project's lead) cannot silently clobber a
-- concurrent update from another worker process. DEFAULT 1 satisfies the CHECK
-- for every existing row, so the column adds cleanly to a populated table.
ALTER TABLE projects
ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
