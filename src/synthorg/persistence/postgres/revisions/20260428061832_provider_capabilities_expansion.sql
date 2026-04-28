-- Create "preset_overrides" table
CREATE TABLE "preset_overrides" (
  "preset_name" text NOT NULL,
  "default_models" jsonb NULL,
  "supported_auth_types" jsonb NULL,
  "candidate_urls" jsonb NULL,
  "base_url" text NULL,
  "updated_at" timestamptz NOT NULL,
  "updated_by" text NOT NULL,
  PRIMARY KEY ("preset_name"),
  CONSTRAINT "preset_overrides_preset_name_check" CHECK (length(TRIM(BOTH FROM preset_name)) > 0),
  CONSTRAINT "preset_overrides_updated_by_check" CHECK (length(TRIM(BOTH FROM updated_by)) > 0)
);
-- Create "provider_audit_events" table
CREATE TABLE "provider_audit_events" (
  "id" bigserial NOT NULL,
  "provider_name" text NOT NULL,
  "event_type" text NOT NULL,
  "actor_id" text NOT NULL,
  "actor_label" text NOT NULL,
  "payload" jsonb NOT NULL DEFAULT '{}',
  "occurred_at" timestamptz NOT NULL,
  PRIMARY KEY ("id"),
  CONSTRAINT "provider_audit_events_actor_id_check" CHECK (length(TRIM(BOTH FROM actor_id)) > 0),
  CONSTRAINT "provider_audit_events_actor_label_check" CHECK (length(TRIM(BOTH FROM actor_label)) > 0),
  CONSTRAINT "provider_audit_events_event_type_check" CHECK (length(TRIM(BOTH FROM event_type)) > 0),
  CONSTRAINT "provider_audit_events_provider_name_check" CHECK (length(TRIM(BOTH FROM provider_name)) > 0)
);
-- Create index "idx_provider_audit_events_occurred" to table: "provider_audit_events"
CREATE INDEX "idx_provider_audit_events_occurred" ON "provider_audit_events" ("occurred_at");
-- Create index "idx_provider_audit_events_provider_id" to table: "provider_audit_events"
CREATE INDEX "idx_provider_audit_events_provider_id" ON "provider_audit_events" ("provider_name", "id" DESC);
