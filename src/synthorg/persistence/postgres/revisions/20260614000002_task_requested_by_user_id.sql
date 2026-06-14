-- depends: 20260614000001_require_webhook_event_type

-- SSE event-stream session ownership: record the human user who filed a task
-- via the API so the /events/stream endpoint can enforce that only the
-- requester (or a CEO) may subscribe to the session keyed by the task id.
-- Nullable: agent-internal tasks have no human requester.

ALTER TABLE tasks
ADD COLUMN requested_by_user_id TEXT;
