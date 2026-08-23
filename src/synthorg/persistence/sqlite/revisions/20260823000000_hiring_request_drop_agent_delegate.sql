-- Strip ``agent_delegate`` from persisted hiring-request payloads.
--
-- ``HiringRequest`` is ``extra="forbid"`` and a row is rebuilt from its
-- ``payload`` JSON, so a key the model no longer declares makes the row fail
-- validation on read. A pending or approved request would then be invisible to
-- the staffing sweep while still holding the one-open-per-role slot, which is
-- the shape that leaves a gate role unstaffed with nothing able to reopen it.
--
-- The key is removed rather than the column dropped: it never had a column.
-- Rows that never carried it are untouched, so this is re-runnable, which
-- matters because yoyo keys applied revisions on the migration id.

UPDATE hiring_requests
SET payload = JSON_REMOVE(payload, '$.agent_delegate')
WHERE JSON_EXTRACT(payload, '$.agent_delegate') IS NOT NULL;
