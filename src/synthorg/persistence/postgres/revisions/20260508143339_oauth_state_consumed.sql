-- Modify "oauth_states" table
ALTER TABLE "oauth_states" ADD COLUMN "consumed_at" timestamptz NULL, ADD COLUMN "connection_name_returned" text NULL;
