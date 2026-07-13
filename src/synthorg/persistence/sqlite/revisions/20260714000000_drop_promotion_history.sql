-- Drop the promotion_history table. The seniority promotion/demotion subsystem
-- has been removed, so its append-only history store has no writer or reader.
-- The idx_promotion_history_agent index is dropped implicitly with the table.

DROP TABLE IF EXISTS promotion_history;
