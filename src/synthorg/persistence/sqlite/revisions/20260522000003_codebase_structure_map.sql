-- depends: 20260522000001_knowledge_substrate

-- Per-project brownfield codebase structure map (#1975). One row per
-- project (1:1 with projects and project_workspaces), recording the
-- deterministic navigable model an import scan produced: modules, entry
-- points, test suites, build files, and declared dependencies (each a
-- JSON array of value objects), plus the source reference scanned and a
-- content hash so a same-source re-import short-circuits when unchanged.
-- ON DELETE CASCADE: deleting a project drops its structure map.

CREATE TABLE codebase_structure_maps (
    project_id TEXT NOT NULL PRIMARY KEY
        CHECK (length(trim(project_id)) > 0),
    source_ref TEXT NOT NULL
        CHECK (length(trim(source_ref)) > 0),
    modules TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(modules) AND json_type(modules) = 'array'),
    entry_points TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(entry_points) AND json_type(entry_points) = 'array'),
    test_suites TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(test_suites) AND json_type(test_suites) = 'array'),
    build_files TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(build_files) AND json_type(build_files) = 'array'),
    dependencies TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(dependencies) AND json_type(dependencies) = 'array'),
    scanned_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
        CHECK (length(content_hash) = 64),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_codebase_structure_maps_content_hash
    ON codebase_structure_maps(content_hash);
