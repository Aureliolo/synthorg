-- Modify "connections" table
ALTER TABLE "connections" ADD CONSTRAINT "connections_webhook_receipt_retention_days_check" CHECK ((webhook_receipt_retention_days IS NULL) OR (webhook_receipt_retention_days >= 0)), ADD COLUMN "webhook_receipt_retention_days" integer NULL;
