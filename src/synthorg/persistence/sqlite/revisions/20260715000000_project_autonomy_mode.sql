-- Per-initiative operator-set autonomy mode.
--
-- Projects gain an optional autonomy_mode: the operator sets an oversight mode
-- per initiative that the SecOps gate resolves against, below a per-agent
-- override and above the department/company default. NULL inherits the
-- department or company autonomy default, so existing rows keep their current
-- behaviour. The closed-set CHECK enforces the AutonomyLevel enum invariant at
-- the row level for any non-Pydantic writer (a self-referencing CHECK on an
-- ADD COLUMN is permitted by SQLite and passes for a NULL value).

ALTER TABLE projects ADD COLUMN autonomy_mode TEXT CHECK (autonomy_mode IN ('full', 'semi', 'supervised', 'locked'));
