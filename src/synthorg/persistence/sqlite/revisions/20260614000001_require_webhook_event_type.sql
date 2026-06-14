-- depends: 20260613000001_perf_indices_and_doc_task_link

-- WebhookReceipt.event_type is now NotBlankStr at the model layer, but the
-- column was created TEXT NOT NULL DEFAULT '' so a blank value could still be
-- stored and would then fail deserialisation (NotBlankStr('') -> MalformedRow).
-- Tighten the column to reject blanks at the database. SQLite cannot ALTER ADD
-- CONSTRAINT, so rebuild the table with the CHECK and drop the empty-string
-- default.

-- Backfill any pre-existing blank rows to a sentinel before the constraint.
UPDATE webhook_receipts SET event_type = 'unknown' WHERE TRIM(event_type) = '';

CREATE TABLE webhook_receipts_new (
    id TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections (name) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (LENGTH(TRIM(event_type)) > 0),
    status TEXT NOT NULL DEFAULT 'received',
    received_at TEXT NOT NULL,
    processed_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

INSERT INTO webhook_receipts_new (
    id, connection_name, event_type, status, received_at, processed_at,
    payload_json, error
)
SELECT
    id, connection_name, event_type, status, received_at, processed_at,
    payload_json, error
FROM webhook_receipts;

DROP TABLE webhook_receipts;
ALTER TABLE webhook_receipts_new RENAME TO webhook_receipts;

CREATE INDEX idx_webhook_receipts_conn_received
ON webhook_receipts (connection_name, received_at DESC);
CREATE INDEX idx_webhook_receipts_received_id
ON webhook_receipts (received_at DESC, id DESC);
