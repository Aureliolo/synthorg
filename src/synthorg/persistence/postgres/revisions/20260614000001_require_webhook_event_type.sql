-- depends: 20260613000001_perf_indices_and_doc_task_link

-- WebhookReceipt.event_type is now NotBlankStr at the model layer, but the
-- column was created TEXT NOT NULL DEFAULT '' so a blank value could still be
-- stored and would then fail deserialisation (NotBlankStr('') -> MalformedRow).
-- Tighten the column to reject blanks at the database: backfill blanks, drop
-- the empty-string default, and add the non-blank CHECK.

UPDATE webhook_receipts SET event_type = 'unknown' WHERE TRIM(event_type) = '';

ALTER TABLE webhook_receipts ALTER COLUMN event_type DROP DEFAULT;

ALTER TABLE webhook_receipts
ADD CONSTRAINT webhook_receipts_event_type_nonblank
CHECK (LENGTH(TRIM(event_type)) > 0);
