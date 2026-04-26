-- Create "idempotency_keys" table
CREATE TABLE `idempotency_keys` (
  `scope` text NOT NULL,
  `key` text NOT NULL,
  `status` text NOT NULL,
  `response_hash` text NULL,
  `response_body` text NULL,
  `created_at` text NOT NULL,
  `expires_at` text NOT NULL,
  PRIMARY KEY (`scope`, `key`),
  CHECK (status IN ('in_flight', 'completed', 'failed'))
);
-- Create index "idx_idempotency_expires" to table: "idempotency_keys"
CREATE INDEX `idx_idempotency_expires` ON `idempotency_keys` (`expires_at`);
