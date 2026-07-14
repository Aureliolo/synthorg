-- Schema changes for the agent-oversight rework.
--
-- Drop the tables of the removed subsystems: the seniority promotion/demotion
-- subsystem and the progressive-trust subsystem have both been removed, so
-- their state/history tables have no writer or reader. Each table's indexes are
-- dropped implicitly with the table.

DROP TABLE IF EXISTS promotion_history;
DROP TABLE IF EXISTS trust_change_history;
DROP TABLE IF EXISTS trust_states;

-- Roles: authority now follows the reporting graph (reports_to), not a
-- seniority rank. SQLite cannot DROP a column referenced by a column CHECK,
-- so rebuild the table. Existing rows get reports_to = NULL; the boot seed
-- re-upserts every built-in role with its correct reporting edge.
ALTER TABLE roles RENAME TO roles_pre_reports_to;
CREATE TABLE roles (
    name TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(name)) > 0),
    department TEXT NOT NULL CHECK (LENGTH(TRIM(department)) > 0),
    required_skills TEXT NOT NULL DEFAULT '[]',
    reports_to TEXT,
    tool_access TEXT NOT NULL DEFAULT '[]',
    system_prompt_template TEXT,
    description TEXT NOT NULL DEFAULT '',
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '%+00:00' OR updated_at LIKE '%Z')
);
INSERT INTO roles (
    name, department, required_skills, reports_to, tool_access,
    system_prompt_template, description, is_builtin, created_at, updated_at
)
SELECT
    name, department, required_skills, NULL, tool_access,
    system_prompt_template, description, is_builtin, created_at, updated_at
FROM roles_pre_reports_to;
DROP TABLE roles_pre_reports_to;
CREATE INDEX idx_roles_department ON roles (department);

-- Org memory: record the author's role for provenance, not a seniority rank.
ALTER TABLE org_facts_operation_log RENAME COLUMN author_seniority TO author_role;
ALTER TABLE org_facts_snapshot RENAME COLUMN author_seniority TO author_role;

-- Training plans no longer carry the new hire's seniority level.
ALTER TABLE training_plans DROP COLUMN new_agent_level;
