-- depends: 20260522000001_knowledge_substrate 20260522000001_dynamic_tools 20260521000002_project_environments

-- Research runs: the durable, replayable record of each research run.
--
-- research_runs: one row per research run. The full run (its brief
-- snapshot, query plan, retrieved items, credibility verdicts, and final
-- report) is stored in run_json; the run is the single source of truth for
-- retrieval, so a recorded run replays deterministically. The brief_id /
-- project_id / status / created_at columns are denormalised copies used for
-- filtering and ordering. project_id is nullable: NULL means a global
-- (project-less) run. ON DELETE CASCADE drops a project's runs when the
-- project is deleted; global runs survive.

CREATE TABLE research_runs (
    run_id TEXT NOT NULL PRIMARY KEY,
    brief_id TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('planning', 'retrieving', 'triaging',
                          'deduplicating', 'synthesising', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    run_json TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_research_runs_created_at
    ON research_runs(created_at DESC, run_id DESC);

CREATE INDEX idx_research_runs_brief
    ON research_runs(brief_id, created_at DESC);

CREATE INDEX idx_research_runs_project
    ON research_runs(project_id, created_at DESC);
