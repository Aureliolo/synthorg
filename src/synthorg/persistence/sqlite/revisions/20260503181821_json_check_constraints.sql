-- Disable the enforcement of foreign-keys constraints
PRAGMA foreign_keys = off;
-- Create "new_provider_audit_events" table
CREATE TABLE `new_provider_audit_events` (
  `id` integer NULL PRIMARY KEY AUTOINCREMENT,
  `provider_name` text NOT NULL,
  `event_type` text NOT NULL,
  `actor_id` text NOT NULL,
  `actor_label` text NOT NULL,
  `payload` text NOT NULL DEFAULT '{}',
  `occurred_at` text NOT NULL,
  CHECK (length(trim(provider_name)) > 0),
  CHECK (length(trim(event_type)) > 0),
  CHECK (length(trim(actor_id)) > 0),
  CHECK (length(trim(actor_label)) > 0),
  CHECK (json_valid(payload)),
  CHECK (length(trim(occurred_at)) > 0)
);
-- Copy rows from old table "provider_audit_events" to new temporary table "new_provider_audit_events"
INSERT INTO `new_provider_audit_events` (`id`, `provider_name`, `event_type`, `actor_id`, `actor_label`, `payload`, `occurred_at`) SELECT `id`, `provider_name`, `event_type`, `actor_id`, `actor_label`, `payload`, `occurred_at` FROM `provider_audit_events`;
-- Drop "provider_audit_events" table after copying rows
DROP TABLE `provider_audit_events`;
-- Rename temporary table "new_provider_audit_events" to "provider_audit_events"
ALTER TABLE `new_provider_audit_events` RENAME TO `provider_audit_events`;
-- Create index "idx_provider_audit_events_provider_id" to table: "provider_audit_events"
CREATE INDEX `idx_provider_audit_events_provider_id` ON `provider_audit_events` (`provider_name`, `id` DESC);
-- Create index "idx_provider_audit_events_occurred" to table: "provider_audit_events"
CREATE INDEX `idx_provider_audit_events_occurred` ON `provider_audit_events` (`occurred_at`);
-- Create "new_preset_overrides" table
CREATE TABLE `new_preset_overrides` (
  `preset_name` text NOT NULL,
  `default_models` text NULL,
  `supported_auth_types` text NULL,
  `candidate_urls` text NULL,
  `base_url` text NULL,
  `updated_at` text NOT NULL,
  `updated_by` text NOT NULL,
  PRIMARY KEY (`preset_name`),
  CHECK (length(trim(preset_name)) > 0),
  CHECK (default_models IS NULL OR json_valid(default_models)),
  CHECK (supported_auth_types IS NULL OR json_valid(supported_auth_types)),
  CHECK (candidate_urls IS NULL OR json_valid(candidate_urls)),
  CHECK (length(trim(updated_at)) > 0),
  CHECK (length(trim(updated_by)) > 0)
);
-- Copy rows from old table "preset_overrides" to new temporary table "new_preset_overrides"
INSERT INTO `new_preset_overrides` (`preset_name`, `default_models`, `supported_auth_types`, `candidate_urls`, `base_url`, `updated_at`, `updated_by`) SELECT `preset_name`, `default_models`, `supported_auth_types`, `candidate_urls`, `base_url`, `updated_at`, `updated_by` FROM `preset_overrides`;
-- Drop "preset_overrides" table after copying rows
DROP TABLE `preset_overrides`;
-- Rename temporary table "new_preset_overrides" to "preset_overrides"
ALTER TABLE `new_preset_overrides` RENAME TO `preset_overrides`;
-- Enable back the enforcement of foreign-keys constraints
PRAGMA foreign_keys = on;
