-- Prompt-cache usage was carried on the record as a boolean that no column
-- stored, so every row read back said nothing about caching and the
-- dashboard's cache figure was computed over an in-memory window only.
-- Counts replace the flag because the bill is proportional to them: a call
-- that reused one cached token and one that reused ninety thousand are both
-- "hits", and only the count says what caching saved. Zero is the honest
-- value for every existing row, since the provider's count was never kept.

ALTER TABLE cost_records
ADD COLUMN cache_read_input_tokens BIGINT NOT NULL DEFAULT 0
CHECK (cache_read_input_tokens >= 0);

ALTER TABLE cost_records
ADD COLUMN cache_write_input_tokens BIGINT NOT NULL DEFAULT 0
CHECK (cache_write_input_tokens >= 0);
