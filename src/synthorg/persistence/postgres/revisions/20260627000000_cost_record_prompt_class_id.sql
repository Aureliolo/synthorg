-- Purpose attribution: the prompt-class id the call was made under, so cost
-- and latency can be sliced by prompt purpose. Nullable: existing rows and
-- calls with no system prompt purpose carry NULL.

ALTER TABLE cost_records ADD COLUMN prompt_class_id TEXT;
