-- depends: 20260603000002_benchmark_scores

-- Secondary indices for unindexed hot query paths surfaced by the perf audit.
-- Each backs a filter/sort that previously forced a full sequential scan.

-- Unfiltered conversation list (ORDER BY created_at DESC, id DESC).
CREATE INDEX idx_conversations_created_id
ON conversations (created_at DESC, id DESC);

-- "List proposals for a conversation" (filter conversation_id,
-- ORDER BY created_at DESC, id DESC -- include the id tiebreaker so the
-- index fully covers the sort and the planner skips a post-sort pass).
CREATE INDEX idx_cp_conversation_id
ON conversational_proposals (conversation_id, created_at DESC, id DESC);

-- "All invites for agent X across conversations" (filter target_agent_id alone).
CREATE INDEX idx_cinv_target_agent_id
ON conversation_invites (target_agent_id);

-- Unfiltered webhook-receipt list (ORDER BY received_at DESC, id DESC).
CREATE INDEX idx_webhook_receipts_received_id
ON webhook_receipts (received_at DESC, id DESC);

-- Denormalise a deliverable doc's related task ids onto the metadata row so
-- receipt assembly can resolve "the deliverable for task X" with one filtered
-- query instead of reading every deliverable's body to inspect the link
-- (N+1 -> 1). Stored as a JSON array string, mirroring ``tags``.
ALTER TABLE project_docs
ADD COLUMN related_task_ids TEXT NOT NULL DEFAULT '[]';
