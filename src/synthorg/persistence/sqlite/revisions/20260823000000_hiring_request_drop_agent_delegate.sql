-- Close out what the removed scaling subsystem left in other tables.
--
-- It owned no tables of its own, which is not the same as having written
-- nothing: it put a key in every hiring-request payload and raised approvals
-- into the shared queue. Both outlive the code that understood them.
--
-- 1. Strip ``agent_delegate`` from persisted hiring-request payloads.
--
-- ``HiringRequest`` is ``extra="forbid"`` and a row is rebuilt from its
-- ``payload`` JSON, so a key the model no longer declares makes the row fail
-- validation on read. A pending or approved request would then be invisible to
-- the staffing sweep while still holding the one-open-per-role slot, which is
-- the shape that leaves a gate role unstaffed with nothing able to reopen it.
--
-- The key is removed rather than the column dropped: it never had a column.
--
-- Unguarded on purpose. ``json_extract`` answers SQL NULL for a key holding
-- JSON null exactly as it does for a key that is absent, and the writer called
-- ``model_dump`` without ``exclude_none``, so the null spelling is what almost
-- every stored row carries: a guard on it skips precisely the rows this exists
-- to repair. ``json_remove`` is already a no-op on a key that is not there, so
-- the guard bought nothing and cost the migration its purpose.

UPDATE hiring_requests
SET payload = JSON_REMOVE(payload, '$.agent_delegate');

-- 2. Expire the approvals the deleted scaling gate raised.
--
-- Those rows are not inert: the queue still offers approve and reject, and
-- approving one now drives nothing at all, because the guard that consumed it
-- is gone. Neither existing safety net reaches them, since the level-triggered
-- orphan sweep keys on ``task_id`` and a scaling item carries none, and
-- delete-time retirement fires on a row being removed rather than a subsystem.
--
-- EXPIRED rather than REJECTED, and ``decided_at`` / ``decided_by`` left NULL,
-- for the reason the retirement path gives: a rejection is a reviewer's
-- verdict, and nobody made one. There is simply nothing left to decide.
--
-- Scoped to the ``scaling:`` prefix the gate wrote and nothing wider, and to
-- rows still pending: a decided one records what a person chose and is not
-- ours to overwrite.

UPDATE approvals
SET status = 'expired'
WHERE
    status = 'pending'
    AND action_type LIKE 'scaling:%';
