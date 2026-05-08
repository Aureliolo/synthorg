-- Create index "idx_cost_records_agent_timestamp" to table: "cost_records"
CREATE INDEX "idx_cost_records_agent_timestamp" ON "cost_records" ("agent_id", "timestamp" DESC);
-- Create index "idx_cost_records_task_timestamp" to table: "cost_records"
CREATE INDEX "idx_cost_records_task_timestamp" ON "cost_records" ("task_id", "timestamp" DESC);
-- Create index "idx_dr_task_recorded_id" to table: "decision_records"
CREATE INDEX "idx_dr_task_recorded_id" ON "decision_records" ("task_id", "recorded_at", "id");
