-- depends: 20260602000001_deliverable_receipts 20260602000001_run_narrative_doc_type

-- Durable red-team report archive. One immutable audit record per
-- ``execution_id`` (single-shot via the primary key) capturing the
-- merged report the red-team gate produced and its aggregate verdict, so
-- the flight-recorder read surface can answer "why was this deliverable
-- sent back?" long after the run finished. The in-process
-- ``RedTeamReportRepository`` put/get handshake is unchanged; this table
-- is the cross-process durability layer. The full report is stored as
-- JSON text in ``report_json``; the structured columns exist for
-- filtering and preview without parsing the blob.

CREATE TABLE red_team_reports (
    execution_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (
        verdict IN ('pass', 'pass_with_findings', 'block')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_rtr_task_id ON red_team_reports (task_id);
CREATE INDEX idx_rtr_verdict ON red_team_reports (verdict);
CREATE INDEX idx_rtr_recorded_at ON red_team_reports (recorded_at);
