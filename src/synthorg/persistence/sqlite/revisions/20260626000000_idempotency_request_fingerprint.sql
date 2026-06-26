-- Idempotency request fingerprint: a hex SHA-256 of the request body that
-- first claimed each idempotency key. Lets the service reject a replay of the
-- same (scope, key) carrying a different payload instead of returning the
-- prior result. Nullable so existing rows and non-opted-in callers stay valid.

ALTER TABLE idempotency_keys ADD COLUMN request_fingerprint TEXT;
