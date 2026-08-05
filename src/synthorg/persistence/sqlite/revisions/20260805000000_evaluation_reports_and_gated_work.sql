-- Two records the loop needed and did not keep.
--
-- 1. The evaluate stage's verdict becomes a record.
--
-- The verdict is the one artefact that decides whether an initiative
-- delivered, and it existed only inside the stage that produced it. An
-- operator whose initiative did not complete could read unmet_count=2 in a
-- log line and nothing else, and a lost compare-and-set race threw away a
-- judgement that cost real money and cannot be re-derived from anything
-- persisted.
--
-- Append-only, keyed unique on (plan_id, attempt): a re-evaluation is a new
-- attempt with its own row rather than an edit of the old one. Overwriting
-- would erase the evidence that the objective was judged and found wanting,
-- which is exactly what the replan points at.
CREATE TABLE initiative_evaluation_report (
    record_id TEXT NOT NULL PRIMARY KEY,
    plan_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    verdict_summary TEXT NOT NULL
    CHECK (LENGTH(TRIM(verdict_summary)) > 0),
    verdicts TEXT NOT NULL,
    objective_met INTEGER NOT NULL CHECK (objective_met IN (0, 1)),
    evaluated_at TEXT NOT NULL
        CHECK (evaluated_at LIKE '%+00:00' OR evaluated_at LIKE '%Z'),
    CONSTRAINT uq_evaluation_report_attempt UNIQUE (plan_id, attempt)
);

CREATE INDEX idx_evaluation_report_plan
ON initiative_evaluation_report (plan_id, evaluated_at DESC);
CREATE INDEX idx_evaluation_report_project
ON initiative_evaluation_report (project_id, evaluated_at DESC);

-- 2. A forecast remembers the work it gated.
--
-- Under budget.forecast_required (the default), submitting an objective
-- minted a pending forecast and raised inside a detached background task.
-- The caller already had its 202, so the automation door accepted work,
-- returned success, and dropped it; approving the forecast afterwards
-- re-dispatched nothing, because nothing had kept the work item.
--
-- Storing it is what makes approval mean "run this". Nullable because a
-- forecast can also be minted for a brief that was never gated, and every
-- row that predates this column is one whose work is already long gone.
ALTER TABLE cost_forecasts ADD COLUMN gated_work_item TEXT;
