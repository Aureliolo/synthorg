-- Durable audit archive for completion-oracle peer-review verdicts.
--
-- One row per execution records the independent reviewer's verdict for a
-- deliverable, so the flight-recorder read surface can answer "why was this
-- deliverable sent back?" long after the run. The full report is JSON in
-- report_json; the structured columns are what the read surface filters and
-- previews on. The row-level CHECK enforces the reviewer-is-distinct invariant
-- for any non-Pydantic writer, mirroring the decision_records table.

CREATE TABLE completion_oracle_reports (
    execution_id TEXT NOT NULL PRIMARY KEY,
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

CREATE INDEX idx_cor_task_id ON completion_oracle_reports (task_id, recorded_at DESC);
CREATE INDEX idx_cor_verdict ON completion_oracle_reports (verdict, recorded_at DESC);
CREATE INDEX idx_cor_recorded_at ON completion_oracle_reports (recorded_at DESC);
