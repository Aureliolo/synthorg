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
-- ``?`` tests top-level key existence regardless of the value, so unlike
-- SQLite's ``json_extract`` it does not confuse a key holding JSON null with a
-- key that is absent, and the guard is a genuine narrowing here.

UPDATE hiring_requests
SET payload = payload - 'agent_delegate'
WHERE payload ? 'agent_delegate';

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

-- 3. Drop the custom rules whose metric no longer exists.
--
-- ``metric_path`` is a plain column with no vocabulary CHECK, so a rule an
-- operator built on ``scaling.total_decisions`` or ``scaling.success_rate``
-- sits in the table quite happily and fails only in Python, where a
-- field validator rejects any path absent from the metric registry.
--
-- One such row takes the whole listing with it, not just itself: the repo
-- builds its result with ``tuple(_row_to_definition(row) for row in rows)``
-- outside the driver-error handler, so the raise escapes as far as
-- ``GET /meta/custom-rules`` and every rule becomes unreadable.
--
-- Deleted rather than disabled, because disabling does not help: an
-- unfiltered list still reads the row and still validates it. Nor can the
-- path be rewritten to something valid, since that would silently change
-- what the operator's rule means. The metric it watches is gone, so the
-- rule can never fire again and cannot be repaired into one that can.

DELETE FROM custom_rules
WHERE metric_path LIKE 'scaling.%';
