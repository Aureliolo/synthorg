-- Agile sprint records: one row per time-boxed work cycle for an
-- agile_kanban project. Backs the /sprints API and the SprintService that
-- pulls tasks into a sprint and advances its strictly-linear lifecycle
-- (planning -> active -> in_review -> retrospective -> completed).
-- start_date / end_date are the domain model's own ISO-8601 strings, so
-- they are stored verbatim as nullable TEXT on both backends.

CREATE TABLE sprints (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    project TEXT CHECK (project IS NULL OR CHAR_LENGTH(TRIM(project)) > 0),
    name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
    CHECK (status IN ('planning', 'active', 'in_review', 'retrospective', 'completed')),
    sprint_number INTEGER NOT NULL CHECK (sprint_number >= 1),
    duration_days INTEGER NOT NULL CHECK (duration_days >= 1),
    start_date TEXT,
    end_date TEXT,
    task_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(task_ids) = 'array'),
    completed_task_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(completed_task_ids) = 'array'),
    task_points JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (JSONB_TYPEOF(task_points) = 'object'),
    story_points_committed DOUBLE PRECISION NOT NULL DEFAULT 0.0
    CHECK (story_points_committed >= 0.0),
    story_points_completed DOUBLE PRECISION NOT NULL DEFAULT 0.0
    CHECK (story_points_completed >= 0.0),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
    CHECK (story_points_completed <= story_points_committed),
    UNIQUE (project, sprint_number)
);
CREATE INDEX idx_sprints_status ON sprints (status);
CREATE INDEX idx_sprints_project_status ON sprints (project, status);
CREATE INDEX idx_sprints_number_id ON sprints (sprint_number DESC, id DESC);
CREATE UNIQUE INDEX idx_sprints_org_wide_number ON sprints (sprint_number)
WHERE project IS NULL;
