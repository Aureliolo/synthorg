-- Create "idempotency_keys" table
CREATE TABLE "idempotency_keys" (
  "scope" text NOT NULL,
  "key" text NOT NULL,
  "status" text NOT NULL,
  "claim_token" text NOT NULL,
  "response_hash" text NULL,
  "response_body" text NULL,
  "created_at" timestamptz NOT NULL,
  "expires_at" timestamptz NOT NULL,
  PRIMARY KEY ("scope", "key"),
  CONSTRAINT "idempotency_keys_check" CHECK (expires_at > created_at),
  CONSTRAINT "idempotency_keys_claim_token_check" CHECK (length(TRIM(BOTH FROM claim_token)) > 0),
  CONSTRAINT "idempotency_keys_key_check" CHECK ((length(TRIM(BOTH FROM key)) > 0) AND (length(key) <= 255)),
  CONSTRAINT "idempotency_keys_response_cache_check" CHECK (((status = 'completed'::text) AND (response_hash IS NOT NULL) AND (response_body IS NOT NULL)) OR ((status = ANY (ARRAY['in_flight'::text, 'failed'::text])) AND (response_hash IS NULL) AND (response_body IS NULL))),
  CONSTRAINT "idempotency_keys_scope_check" CHECK ((length(TRIM(BOTH FROM scope)) > 0) AND (length(scope) <= 64)),
  CONSTRAINT "idempotency_keys_status_check" CHECK (status = ANY (ARRAY['in_flight'::text, 'completed'::text, 'failed'::text]))
);
-- Create index "idx_idempotency_expires" to table: "idempotency_keys"
CREATE INDEX "idx_idempotency_expires" ON "idempotency_keys" ("expires_at");
