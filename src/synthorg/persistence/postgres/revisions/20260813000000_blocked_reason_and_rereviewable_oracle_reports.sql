-- Two facts the completion gate could not record.
--
-- 1. WHY a task is blocked.
--
-- ``BLOCKED`` is reached from several directions that mean different
-- things. A completion review that escalates parks the task for a human. A
-- coordination wave releasing a subtask nobody will run parks it for a
-- scheduler. The status is the same in both, so a rule written for the
-- first silently applied to the second: the review gate skipped its
-- peer-review oracle for any blocked task, including ones no human had
-- ever been asked about, and the human-decision path could then carry them
-- to COMPLETED without that review ever running.
--
-- ``blocked_reason`` is stamped by whichever writer parks the task, so the
-- rule reads the reason rather than inferring it from a status several
-- paths produce. NULL is the honest value for rows written before anyone
-- said, and it is deliberately NOT a synonym for any member: a rule that
-- treats an unnamed reason as its own reintroduces exactly the conflation
-- this column removes. The CHECK matches ``BlockedReason`` so a direct
-- write cannot persist a row the domain model then refuses to parse.
--
-- 2. That an execution was reviewed more than once.
--
-- ``completion_oracle_reports`` was keyed on ``execution_id`` alone, but
-- the gate runs again whenever a task is decided, re-opened and decided
-- again. The second report collided with the first and was swallowed as a
-- warning, so the decision that actually stood was the one with no
-- evidence behind it, while a superseded verdict remained the only record.
-- Live rows show the collision: two reports per task, and a
-- UniqueViolation logged between them.
--
-- A report is one review event, not one execution, so a surrogate identity
-- column carries it and ``execution_id`` keeps an index instead of a
-- constraint. The read path already orders by ``recorded_at DESC`` and
-- returns a list, so nothing depended on there being exactly one.

ALTER TABLE tasks
ADD COLUMN blocked_reason TEXT CHECK (
    blocked_reason IN ('oracle_escalated', 'wave_released')
);

ALTER TABLE completion_oracle_reports
DROP CONSTRAINT completion_oracle_reports_pkey;

ALTER TABLE completion_oracle_reports
ADD COLUMN report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY;

CREATE INDEX idx_cor_execution_id
ON completion_oracle_reports (execution_id, recorded_at DESC);
