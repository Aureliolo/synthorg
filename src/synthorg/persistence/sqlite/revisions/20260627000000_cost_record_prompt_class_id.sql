-- Purpose attribution: the prompt-class id the call was made under, so cost
-- can be sliced by prompt purpose. Nullable: NULL when no system prompt
-- purpose applies to the call.

ALTER TABLE cost_records ADD COLUMN prompt_class_id TEXT;
