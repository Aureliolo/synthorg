-- One open hiring request per role, enforced by the database.
--
-- The rule already existed: a role is staffed by one hire at a time, and the
-- staffing sweep checks before opening another. The check read an in-memory
-- map hydrated at boot, so it held only because exactly one process owns that
-- map. Nothing at this level stopped a second writer, and nothing repaired the
-- duplicates once they existed: a live run finished with five open requests
-- for one role, each a re-ask of the same question, because rejecting one
-- freed the slot the sweep then filled again.
--
-- PENDING and APPROVED are the open statuses. Approval and instantiation are
-- separate steps, so a request a human approved but that has registered nobody
-- yet is still a hire under way. REJECTED and INSTANTIATED are answered, and a
-- later gap for the same role is a new question rather than that one.
--
-- The surplus rows are closed before the index is built, or the index cannot
-- be created on any database that carries them and the backend crash-loops on
-- boot. ``status`` is denormalised out of ``payload``, which is what the row is
-- actually rebuilt from, so both are written or the repaired row reads back
-- with its old status.
--
-- ``IF NOT EXISTS`` keeps this re-runnable: yoyo keys applied revisions on the
-- migration id rather than on content, so a database that already carries the
-- index reads as not having run this one and gets the file again.

UPDATE hiring_requests
SET
    status = 'rejected',
    payload = JSONB_SET(payload, '{status}', '"rejected"'::JSONB)
WHERE
    status IN ('pending', 'approved')
    AND id NOT IN (
        SELECT id FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY role ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM hiring_requests
            WHERE status IN ('pending', 'approved')
        ) AS ranked
        WHERE rn = 1
    );

CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_requests_one_open_per_role
ON hiring_requests (role)
WHERE status IN ('pending', 'approved');
