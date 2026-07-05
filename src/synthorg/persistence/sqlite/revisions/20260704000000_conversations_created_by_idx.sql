-- Index the owner filter: the conversation-history drawer lists
-- conversations WHERE created_by = ? ORDER BY created_at DESC, id DESC, so
-- a composite index keeps that owner-scoped seek-page off a full table
-- scan as the conversation log grows.

CREATE INDEX idx_conversations_created_by_created_id
ON conversations (created_by, created_at DESC, id DESC);
