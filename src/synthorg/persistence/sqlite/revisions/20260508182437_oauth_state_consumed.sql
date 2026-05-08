-- Disable the enforcement of foreign-keys constraints
PRAGMA foreign_keys = off;
-- Create "new_oauth_states" table
CREATE TABLE `new_oauth_states` (
  `state_token` text NOT NULL,
  `connection_name` text NOT NULL,
  `pkce_verifier` text NULL,
  `scopes_requested` text NOT NULL DEFAULT '',
  `redirect_uri` text NOT NULL DEFAULT '',
  `created_at` text NOT NULL,
  `expires_at` text NOT NULL,
  `consumed_at` text NULL,
  `connection_name_returned` text NULL,
  PRIMARY KEY (`state_token`),
  CONSTRAINT `0` FOREIGN KEY (`connection_name`) REFERENCES `connections` (`name`) ON UPDATE NO ACTION ON DELETE CASCADE,
  CONSTRAINT `oauth_states_consumed_pair` CHECK (
        (consumed_at IS NULL AND connection_name_returned IS NULL)
        OR
        (consumed_at IS NOT NULL AND connection_name_returned IS NOT NULL)
    )
);
-- Copy rows from old table "oauth_states" to new temporary table "new_oauth_states"
INSERT INTO `new_oauth_states` (`state_token`, `connection_name`, `pkce_verifier`, `scopes_requested`, `redirect_uri`, `created_at`, `expires_at`) SELECT `state_token`, `connection_name`, `pkce_verifier`, `scopes_requested`, `redirect_uri`, `created_at`, `expires_at` FROM `oauth_states`;
-- Drop "oauth_states" table after copying rows
DROP TABLE `oauth_states`;
-- Rename temporary table "new_oauth_states" to "oauth_states"
ALTER TABLE `new_oauth_states` RENAME TO `oauth_states`;
-- Create index "idx_oauth_states_expires" to table: "oauth_states"
CREATE INDEX `idx_oauth_states_expires` ON `oauth_states` (`expires_at`);
-- Create index "idx_oauth_states_connection" to table: "oauth_states"
CREATE INDEX `idx_oauth_states_connection` ON `oauth_states` (`connection_name`);
-- Create index "idx_oauth_states_consumed" to table: "oauth_states"
CREATE INDEX `idx_oauth_states_consumed` ON `oauth_states` (`consumed_at`);
-- Enable back the enforcement of foreign-keys constraints
PRAGMA foreign_keys = on;
