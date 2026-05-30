-- depends: 20260522000003_codebase_structure_map

-- Long-horizon project brain. Append-only structured project
-- state: decisions, open questions, blockers, risks, dependencies, and
-- plan revisions. A change to a logical entry is a new row (same
-- entry_id, revision incremented); the current state is the latest
-- revision per entry_id, computed with a window function. payload is a
-- kind-discriminated JSON object; related ids, tags, and citations are
-- JSON arrays. The git workspace holds a versioned snapshot; this table
-- is the authoritative structured store. ON DELETE CASCADE: deleting a
-- project drops its brain.

CREATE TABLE project_brain_entries (
    project_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(project_id)) > 0),
    entry_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(entry_id)) > 0),
    revision INTEGER NOT NULL
    CHECK (revision >= 1),
    entry_kind TEXT NOT NULL
    CHECK (entry_kind IN (
        'decision', 'open_question', 'blocker', 'risk',
        'dependency', 'plan_revision'
    )),
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL,
    author TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    related_task_ids TEXT NOT NULL DEFAULT '[]',
    related_entry_ids TEXT NOT NULL DEFAULT '[]',
    supersedes_entry_id TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    confidence DOUBLE PRECISION,
    citations TEXT NOT NULL DEFAULT '[]',
    payload TEXT NOT NULL,
    PRIMARY KEY (project_id, entry_id, revision),
    -- Redundant with the PK for per-project lookups, kept deliberately: it
    -- enforces a globally unique (entry_id, revision) pair, so a revision is
    -- addressable across projects without the project_id prefix.
    UNIQUE (entry_id, revision),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_project_brain_current
ON project_brain_entries (project_id, entry_id, revision DESC);

CREATE INDEX idx_project_brain_kind
ON project_brain_entries (project_id, entry_kind);

CREATE INDEX idx_project_brain_status
ON project_brain_entries (project_id, status);

CREATE INDEX idx_project_brain_recorded
ON project_brain_entries (project_id, recorded_at DESC);

CREATE INDEX idx_project_brain_author
ON project_brain_entries (project_id, author);

-- Tracks the highest brain revision per entry confirmed present in the RAG
-- index. A mutable bookkeeping projection (upsert), distinct from the
-- append-only entries log above: the write path persists the SQL row before the
-- best-effort index, so a transient index failure leaves a gap. Boot replay
-- diffs each entry's current revision against last_indexed_revision here and
-- re-indexes only the gap, so a never-revised entry whose index write failed
-- still becomes searchable. ON DELETE CASCADE: deleting a project drops it.
CREATE TABLE project_brain_index_state (
    project_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(project_id)) > 0),
    entry_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(entry_id)) > 0),
    last_indexed_revision INTEGER NOT NULL
    CHECK (last_indexed_revision >= 1),
    PRIMARY KEY (project_id, entry_id),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);
