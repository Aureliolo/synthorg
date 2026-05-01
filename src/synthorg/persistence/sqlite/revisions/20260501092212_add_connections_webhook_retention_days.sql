-- Disable the enforcement of foreign-keys constraints
PRAGMA foreign_keys = off;
-- Create "new_connections" table
CREATE TABLE `new_connections` (
  `name` text NOT NULL,
  `connection_type` text NOT NULL,
  `auth_method` text NOT NULL,
  `base_url` text NULL,
  `secret_refs_json` text NOT NULL DEFAULT '[]',
  `rate_limit_rpm` integer NOT NULL DEFAULT 0,
  `rate_limit_concurrent` integer NOT NULL DEFAULT 0,
  `health_check_enabled` integer NOT NULL DEFAULT 1,
  `health_status` text NOT NULL DEFAULT 'unknown',
  `last_health_check_at` text NULL,
  `metadata_json` text NOT NULL DEFAULT '{}',
  `webhook_receipt_retention_days` integer NULL,
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  PRIMARY KEY (`name`),
  CHECK (length(name) > 0),
  CHECK (
        connection_type IN (
            'github', 'slack', 'smtp', 'database',
            'generic_http', 'oauth_app', 'a2a_peer'
        )
    ),
  CHECK (
        auth_method IN (
            'api_key', 'oauth2', 'basic_auth',
            'bearer_token', 'custom'
        )
    ),
  CHECK (rate_limit_rpm >= 0),
  CHECK (rate_limit_concurrent >= 0),
  CHECK (health_check_enabled IN (0, 1)),
  CHECK (
            health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')
        ),
  CHECK (
            webhook_receipt_retention_days IS NULL
            OR webhook_receipt_retention_days >= 0
        )
);
-- Copy rows from old table "connections" to new temporary table "new_connections"
INSERT INTO `new_connections` (`name`, `connection_type`, `auth_method`, `base_url`, `secret_refs_json`, `rate_limit_rpm`, `rate_limit_concurrent`, `health_check_enabled`, `health_status`, `last_health_check_at`, `metadata_json`, `created_at`, `updated_at`) SELECT `name`, `connection_type`, `auth_method`, `base_url`, `secret_refs_json`, `rate_limit_rpm`, `rate_limit_concurrent`, `health_check_enabled`, `health_status`, `last_health_check_at`, `metadata_json`, `created_at`, `updated_at` FROM `connections`;
-- Drop "connections" table after copying rows
DROP TABLE `connections`;
-- Rename temporary table "new_connections" to "connections"
ALTER TABLE `new_connections` RENAME TO `connections`;
-- Create index "idx_connections_type" to table: "connections"
CREATE INDEX `idx_connections_type` ON `connections` (`connection_type`);
-- Enable back the enforcement of foreign-keys constraints
PRAGMA foreign_keys = on;
