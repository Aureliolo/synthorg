-- Create "idempotency_keys" table
CREATE TABLE "idempotency_keys" (
  "scope" text NOT NULL,
  "key" text NOT NULL,
  "status" text NOT NULL,
  "response_hash" text NULL,
  "response_body" jsonb NULL,
  "created_at" timestamptz NOT NULL,
  "expires_at" timestamptz NOT NULL,
  PRIMARY KEY ("scope", "key"),
  CONSTRAINT "idempotency_keys_key_check" CHECK (length(key) > 0),
  CONSTRAINT "idempotency_keys_scope_check" CHECK (length(scope) > 0),
  CONSTRAINT "idempotency_keys_status_check" CHECK (status = ANY (ARRAY['in_flight'::text, 'completed'::text, 'failed'::text]))
);
-- Create index "idx_idempotency_expires" to table: "idempotency_keys"
CREATE INDEX "idx_idempotency_expires" ON "idempotency_keys" ("expires_at");
