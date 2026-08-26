-- Where a decomposition has got to, on the plan it is writing.
--
-- A recursive decomposition persists its tree once, at the end, so the plan
-- row reads PLANNING with zero items for as long as the planning runs. That is
-- correct and it leaves the operator with nothing: a live run sat at zero for
-- 54 minutes while the plan page promised "items appear as they are written",
-- and the only way to tell a working decomposition from a hung one was the
-- backend log. The session ledger bounding the run holds every number the
-- question needs (sessions spent against the tree limit, the level reached,
-- the units written so far) and held them only in memory, so a restart lost
-- them and no surface could read them in the first place.
--
-- Nullable with no default, because absent and zero are different claims: NULL
-- is "nothing has reported", which covers every plan written before this
-- column existed as well as one nothing is decomposing, while a zero snapshot
-- is a decomposition that has started and not yet finished a node. A DEFAULT
-- would make every historical plan assert the latter.
--
-- One JSONB column rather than four scalars. The whole value is a display
-- snapshot read as a unit and overwritten as a unit; nothing filters or
-- aggregates on any part of it, and four columns would be four chances for a
-- writer to update some of them. It follows ``review`` and ``version_history``
-- on this table, which are the same shape for the same reason.
--
-- The object type is asserted rather than assumed: the writer serialises a
-- frozen Pydantic model, so a scalar or an array can only arrive from a path
-- that bypassed it, and that is exactly the write worth refusing at this
-- level. JSONB parses on write, so validity itself needs no check here as it
-- does on the SQLite side.

ALTER TABLE plans ADD COLUMN IF NOT EXISTS decomposition_progress JSONB;

ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_decomposition_progress_check;

ALTER TABLE plans ADD CONSTRAINT plans_decomposition_progress_check
CHECK (
    decomposition_progress IS NULL
    OR JSONB_TYPEOF(decomposition_progress) = 'object'
);
