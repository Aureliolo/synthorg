-- transactional: false
-- Conversational plan review: make a plan-item comment reply-bearing.
--
-- A comment gains an author kind (a human operator or a responding agent), the
-- id of the responding agent when an agent wrote it, and the id of the comment
-- it answers when it is a reply. The item is still the thread: reply_to_id is a
-- flat parent link, not a nested tree, and a self-referential FK keeps a reply
-- from pointing at a comment that does not exist (ON DELETE SET NULL demotes a
-- reply to top-level if its parent is ever removed). The author_agent_id CHECK
-- also pairs it with author_kind (an agent comment carries an agent id, a human
-- comment carries none), so the Pydantic authorship invariant holds at the row.
-- All columns are additive with valid defaults for existing rows (author_kind
-- defaults to the historic 'human', the agent id and reply link default NULL),
-- so they add cheaply to a populated table. This migration runs outside a
-- transaction so the reply index can be built CONCURRENTLY without blocking
-- live writes on the plan-review path.

ALTER TABLE plan_item_comments
ADD COLUMN author_kind TEXT NOT NULL DEFAULT 'human'
CHECK (author_kind IN ('human', 'agent')),
ADD COLUMN author_agent_id TEXT
CHECK (
    (author_agent_id IS NULL OR CHAR_LENGTH(TRIM(author_agent_id)) > 0)
    AND ((author_kind = 'agent') = (author_agent_id IS NOT NULL))
),
ADD COLUMN reply_to_id TEXT
REFERENCES plan_item_comments (id) ON DELETE SET NULL
CHECK (reply_to_id IS NULL OR CHAR_LENGTH(TRIM(reply_to_id)) > 0);

CREATE INDEX CONCURRENTLY idx_plan_item_comments_reply
ON plan_item_comments (reply_to_id)
WHERE reply_to_id IS NOT NULL;
