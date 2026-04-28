-- Create "provider_audit_events" table
CREATE TABLE `provider_audit_events` (
  `id` integer NULL PRIMARY KEY AUTOINCREMENT,
  `provider_name` text NOT NULL,
  `event_type` text NOT NULL,
  `actor_id` text NOT NULL,
  `actor_label` text NOT NULL,
  `payload_json` text NOT NULL DEFAULT '{}',
  `occurred_at` text NOT NULL,
  CHECK (length(trim(provider_name)) > 0),
  CHECK (length(trim(event_type)) > 0),
  CHECK (length(trim(actor_id)) > 0),
  CHECK (length(trim(actor_label)) > 0),
  CHECK (length(trim(occurred_at)) > 0)
);
-- Create index "idx_provider_audit_events_provider_id" to table: "provider_audit_events"
CREATE INDEX `idx_provider_audit_events_provider_id` ON `provider_audit_events` (`provider_name`, `id` DESC);
-- Create index "idx_provider_audit_events_occurred" to table: "provider_audit_events"
CREATE INDEX `idx_provider_audit_events_occurred` ON `provider_audit_events` (`occurred_at`);
-- Create "preset_overrides" table
CREATE TABLE `preset_overrides` (
  `preset_name` text NOT NULL,
  `default_models_json` text NULL,
  `supported_auth_types_json` text NULL,
  `candidate_urls_json` text NULL,
  `base_url` text NULL,
  `updated_at` text NOT NULL,
  `updated_by` text NOT NULL,
  PRIMARY KEY (`preset_name`),
  CHECK (length(trim(preset_name)) > 0),
  CHECK (length(trim(updated_at)) > 0),
  CHECK (length(trim(updated_by)) > 0)
);
