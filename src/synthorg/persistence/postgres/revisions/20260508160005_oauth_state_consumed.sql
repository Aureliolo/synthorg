-- Modify "oauth_states" table
ALTER TABLE "oauth_states" ADD COLUMN "consumed_at" timestamptz NULL, ADD COLUMN "connection_name_returned" text NULL;
-- Create index "idx_oauth_states_consumed" to table: "oauth_states"
CREATE INDEX "idx_oauth_states_consumed" ON "oauth_states" ("consumed_at");
