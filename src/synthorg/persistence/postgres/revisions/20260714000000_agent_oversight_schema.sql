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
-- seniority rank. Existing rows get reports_to = NULL; the boot seed re-upserts
-- every built-in role with its correct reporting edge.
ALTER TABLE roles DROP COLUMN authority_level;
ALTER TABLE roles ADD COLUMN reports_to TEXT;

-- Org memory: record the author's role for provenance, not a seniority rank.
ALTER TABLE org_facts_operation_log RENAME COLUMN author_seniority TO author_role;
ALTER TABLE org_facts_snapshot RENAME COLUMN author_seniority TO author_role;

-- Training plans no longer carry the new hire's seniority level.
ALTER TABLE training_plans DROP COLUMN new_agent_level;
