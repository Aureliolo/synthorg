-- Schema changes for the agent-oversight rework.
--
-- Drop the tables of the removed subsystems: the seniority promotion/demotion
-- subsystem and the progressive-trust subsystem have both been removed, so
-- their state/history tables have no writer or reader. Each table's indexes are
-- dropped implicitly with the table.

DROP TABLE IF EXISTS promotion_history;
DROP TABLE IF EXISTS trust_change_history;
DROP TABLE IF EXISTS trust_states;
