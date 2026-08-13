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
-- A report is one review event, not one execution, so a surrogate
-- ``report_id`` carries identity and ``execution_id`` keeps an index instead
-- of a constraint. The read path already orders by ``recorded_at DESC`` and
-- returns a list, so nothing depended on there being exactly one. The column
-- is named identically on both backends: the drift gate compares them by
-- name, and a backend-flavoured spelling reads as a missing column.
--
-- SQLite cannot drop a PRIMARY KEY, so the table is rebuilt (create-new,
-- copy, drop, rename) and its indexes recreated. Nothing references it by
-- foreign key, so the drop fires no cascades. ``tasks`` only gains a
-- nullable column, which ADD COLUMN handles in place.

ALTER TABLE tasks
ADD COLUMN blocked_reason TEXT CHECK (
    blocked_reason IN ('oracle_escalated', 'wave_released')
);

CREATE TABLE completion_oracle_reports_new (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL,
    executor_agent_id TEXT NOT NULL CHECK (executor_agent_id != reviewer_agent_id),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('approve', 'approve_with_notes', 'reject', 'escalate')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

INSERT INTO completion_oracle_reports_new (
    execution_id, task_id, reviewer_agent_id, executor_agent_id, verdict,
    finding_count, report_summary, report_json, recorded_at
)
SELECT
    execution_id,
    task_id,
    reviewer_agent_id,
    executor_agent_id,
    verdict,
    finding_count,
    report_summary,
    report_json,
    recorded_at
FROM completion_oracle_reports;

DROP TABLE completion_oracle_reports;

ALTER TABLE completion_oracle_reports_new RENAME TO completion_oracle_reports;

CREATE INDEX idx_cor_task_id ON completion_oracle_reports (task_id, recorded_at DESC);
CREATE INDEX idx_cor_verdict ON completion_oracle_reports (verdict, recorded_at DESC);
CREATE INDEX idx_cor_recorded_at ON completion_oracle_reports (recorded_at DESC);
CREATE INDEX idx_cor_execution_id
ON completion_oracle_reports (execution_id, recorded_at DESC);
