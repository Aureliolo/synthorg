-- Principle-override table for the rollback executor's PromptMutator.
--
-- See docs/reference/persistence-boundary.md and the canonical schema
-- at ../schema.sql for the per-backend rationale. Overlays the
-- read-only YAML principle packs loaded by engine/strategy/principles.py
-- so a rollback operation can restore previous principle text at
-- runtime without rewriting the packs.
CREATE TABLE principle_overrides (
    scope TEXT NOT NULL PRIMARY KEY
        CHECK (length(trim(scope)) > 0),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    restored_from TEXT NOT NULL CHECK (length(trim(restored_from)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
