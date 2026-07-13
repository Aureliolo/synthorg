-- Company-grade plan review: extend the durable Plan with the review-surface
-- fields and add a per-item comment thread.
--
-- objective_title denormalises the objective's human title onto the plan so the
-- review surface never resolves (or falls back to) a raw id. review holds the
-- consolidated stakeholder-panel review (JSONB, null until reviewed).
-- open_questions / assumptions are JSONB string arrays the owner surfaces for the
-- human. version_history is a JSONB array of prior-version snapshots, for diffing.
-- plan_item_comments is an async discussion thread keyed by (plan_id, item_id),
-- written independently of the version-guarded plan row so a comment never
-- conflicts with a plan rework.

ALTER TABLE plans
    ADD COLUMN objective_title TEXT NOT NULL DEFAULT '',
    ADD COLUMN review JSONB,
    ADD COLUMN open_questions JSONB NOT NULL DEFAULT '[]'::JSONB,
    ADD COLUMN assumptions JSONB NOT NULL DEFAULT '[]'::JSONB,
    ADD COLUMN version_history JSONB NOT NULL DEFAULT '[]'::JSONB;

-- Backfill any pre-existing plan's title from its objective id (no human title
-- was captured before this migration); new plans always carry a real title.
UPDATE plans SET objective_title = objective_id WHERE objective_title = '';

CREATE TABLE plan_item_comments (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    plan_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(plan_id)) > 0),
    item_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(item_id)) > 0),
    author TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(author)) > 0),
    body TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(body)) > 0),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_plan_item_comments_plan ON plan_item_comments (plan_id);
CREATE INDEX idx_plan_item_comments_plan_item
ON plan_item_comments (plan_id, item_id, created_at);
