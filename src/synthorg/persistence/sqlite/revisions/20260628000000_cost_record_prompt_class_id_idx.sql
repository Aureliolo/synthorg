-- Index the prompt-class filter: the cost-by-prompt-purpose dashboard filters
-- cost_records on prompt_class_id (and orders by timestamp DESC), so a
-- composite index keeps that slice off a full table scan as the ledger grows.

CREATE INDEX idx_cost_records_prompt_class_timestamp
ON cost_records (prompt_class_id, timestamp DESC);
