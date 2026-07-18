-- Conversational plan review: make a plan-item comment reply-bearing.
--
-- A comment gains an author kind (a human operator or a responding agent), the
-- id of the responding agent when an agent wrote it, and the id of the comment
-- it answers when it is a reply. The item is still the thread: reply_to_id is a
-- flat parent link, not a nested tree. All three columns are additive with
-- valid defaults for existing rows (kind defaults to the historic 'human',
-- agent id and reply link default NULL), so they add without a table rebuild.
-- The reply_to_id index serves the "replies to this comment" lookup the thread
-- renderer issues.

ALTER TABLE plan_item_comments
ADD COLUMN author_kind TEXT NOT NULL DEFAULT 'human'
CHECK (author_kind IN ('human', 'agent'));

ALTER TABLE plan_item_comments
ADD COLUMN author_agent_id TEXT
CHECK (author_agent_id IS NULL OR LENGTH(TRIM(author_agent_id)) > 0);

ALTER TABLE plan_item_comments
ADD COLUMN reply_to_id TEXT
CHECK (reply_to_id IS NULL OR LENGTH(TRIM(reply_to_id)) > 0);

CREATE INDEX idx_plan_item_comments_reply
ON plan_item_comments (reply_to_id);
