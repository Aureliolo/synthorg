-- depends: 20260619000001_durable_meta_verticals

-- Durable backing for the meta-loop apply paths: active constitutional
-- principles applied by the prompt-tuning altitude, a first-class role
-- registry, and durable departments. These replace the YAML-only principle
-- read path, the static BUILTIN_ROLES catalog, and the in-memory department
-- service so the self-improvement appliers persist real, restart-surviving
-- state instead of no-op stubs.

CREATE TABLE active_principles (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    principle_text TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(principle_text)) > 0),
    scope TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(scope)) > 0),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('all', 'role', 'department')),
    evolution_mode TEXT NOT NULL
    CHECK (evolution_mode IN ('org_wide', 'override', 'advisory')),
    severity TEXT NOT NULL
    CHECK (severity IN ('informational', 'warning', 'critical')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- The cached read provider filters the snapshot by (scope_kind, scope); the
-- index keeps a scoped reload cheap as the table grows.
CREATE INDEX idx_active_principles_scope ON active_principles (scope_kind, scope);
-- ``list_items`` pages newest-first across all active principles.
CREATE INDEX idx_active_principles_created ON active_principles (created_at DESC);

CREATE TABLE roles (
    name TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    department TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(department)) > 0),
    required_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    authority_level TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(authority_level)) > 0),
    tool_access JSONB NOT NULL DEFAULT '[]'::jsonb,
    system_prompt_template TEXT,
    description TEXT NOT NULL DEFAULT '',
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- ``list_items`` pages roles alphabetically by name; the index keeps the
-- in-use lookups and seed upserts cheap.
CREATE INDEX idx_roles_department ON roles (department);

CREATE TABLE departments (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL UNIQUE CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- ``list_departments`` pages newest-first by created_at.
CREATE INDEX idx_departments_created ON departments (created_at DESC);
