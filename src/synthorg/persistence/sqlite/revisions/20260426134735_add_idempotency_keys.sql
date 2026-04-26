-- Create "idempotency_keys" table
CREATE TABLE `idempotency_keys` (
  `scope` text NOT NULL,
  `key` text NOT NULL,
  `status` text NOT NULL,
  `claim_token` text NOT NULL,
  `response_hash` text NULL,
  `response_body` text NULL,
  `created_at` text NOT NULL,
  `expires_at` text NOT NULL,
  PRIMARY KEY (`scope`, `key`),
  CHECK (length(trim(scope)) > 0 AND length(scope) <= 64),
  CHECK (length(trim(key)) > 0 AND length(key) <= 255),
  CHECK (status IN ('in_flight', 'completed', 'failed')),
  CHECK (length(trim(claim_token)) > 0),
  CHECK (length(trim(created_at)) > 0),
  CHECK (
        length(trim(expires_at)) > 0
        AND expires_at > created_at
    ),
  CONSTRAINT `idempotency_keys_response_cache_check` CHECK (
        (status = 'completed'
            AND response_hash IS NOT NULL
            AND response_body IS NOT NULL)
        OR (status IN ('in_flight', 'failed')
            AND response_hash IS NULL
            AND response_body IS NULL)
    )
);
-- Create index "idx_idempotency_expires" to table: "idempotency_keys"
CREATE INDEX `idx_idempotency_expires` ON `idempotency_keys` (`expires_at`);
