-- depends: 20260522000001_knowledge_substrate

-- Per-project brownfield codebase structure map (#1975). One row per
-- project (1:1 with projects and project_workspaces), recording the
-- deterministic navigable model an import scan produced: modules, entry
-- points, test suites, build files, and declared dependencies (each a
-- JSONB array of value objects), plus the source reference scanned and a
-- content hash so a same-source re-import short-circuits when unchanged.
-- ON DELETE CASCADE: deleting a project drops its structure map.

CREATE TABLE codebase_structure_maps (
    project_id TEXT NOT NULL PRIMARY KEY
        CHECK (length(trim(project_id)) > 0),
    source_ref TEXT NOT NULL
        CHECK (length(trim(source_ref)) > 0),
    modules JSONB NOT NULL DEFAULT '[]'::jsonb,
    entry_points JSONB NOT NULL DEFAULT '[]'::jsonb,
    test_suites JSONB NOT NULL DEFAULT '[]'::jsonb,
    build_files JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    scanned_at TIMESTAMPTZ NOT NULL,
    content_hash TEXT NOT NULL
        CHECK (length(content_hash) = 64),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_codebase_structure_maps_content_hash
    ON codebase_structure_maps(content_hash);
