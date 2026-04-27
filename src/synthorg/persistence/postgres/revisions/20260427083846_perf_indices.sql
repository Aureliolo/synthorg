-- Create index "idx_approvals_status_created_at" to table: "approvals"
CREATE INDEX "idx_approvals_status_created_at" ON "approvals" ("status", "created_at" DESC);
-- Create index "idx_oplog_category_ts" to table: "org_facts_operation_log"
CREATE INDEX "idx_oplog_category_ts" ON "org_facts_operation_log" ("category", "timestamp" DESC);
-- Create index "idx_rt_session_used" to table: "refresh_tokens"
CREATE INDEX "idx_rt_session_used" ON "refresh_tokens" ("session_id", "used");
