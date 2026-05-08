-- Add column "consumed_at" to table: "oauth_states"
ALTER TABLE `oauth_states` ADD COLUMN `consumed_at` text NULL;
-- Add column "connection_name_returned" to table: "oauth_states"
ALTER TABLE `oauth_states` ADD COLUMN `connection_name_returned` text NULL;
-- Create index "idx_oauth_states_consumed" to table: "oauth_states"
CREATE INDEX `idx_oauth_states_consumed` ON `oauth_states` (`consumed_at`);
