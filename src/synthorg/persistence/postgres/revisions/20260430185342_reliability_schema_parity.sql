-- Create index "idx_api_keys_user_created_id" to table: "api_keys"
CREATE INDEX "idx_api_keys_user_created_id" ON "api_keys" ("user_id", "created_at", "id");
-- Create index "idx_parked_contexts_agent_parked_at" to table: "parked_contexts"
CREATE INDEX "idx_parked_contexts_agent_parked_at" ON "parked_contexts" ("agent_id", "parked_at" DESC);
