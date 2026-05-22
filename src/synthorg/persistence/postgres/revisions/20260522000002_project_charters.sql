-- depends: 20260521000002_project_environments 20260522000001_dynamic_tools 20260522000001_knowledge_substrate

-- Project charters (deep CEO interview to project charter).
--
-- See ``synthorg/persistence/sqlite/revisions/20260522000002_project_charters.sql``
-- for the design notes on the lifecycle state machine, the
-- existing-vs-new project binding XOR, and the approval-coupling
-- invariant.

CREATE TABLE project_charters (
    id TEXT NOT NULL PRIMARY KEY CHECK(char_length(trim(id)) > 0),
    conversation_id TEXT NOT NULL CHECK(char_length(trim(conversation_id)) > 0),
    created_by TEXT NOT NULL CHECK(char_length(trim(created_by)) > 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    status TEXT NOT NULL DEFAULT 'drafted' CHECK(
        status IN ('drafted', 'approved', 'cancelled')
    ),
    title TEXT NOT NULL CHECK(char_length(trim(title)) > 0),
    brief TEXT NOT NULL CHECK(char_length(trim(brief)) > 0),
    goals TEXT NOT NULL DEFAULT '[]',
    constraints TEXT NOT NULL DEFAULT '[]',
    success_criteria TEXT NOT NULL DEFAULT '[]',
    in_scope TEXT NOT NULL DEFAULT '[]',
    out_of_scope TEXT NOT NULL DEFAULT '[]',
    envelope_amount DOUBLE PRECISION NOT NULL CHECK(envelope_amount > 0),
    envelope_currency TEXT NOT NULL CHECK(char_length(envelope_currency) = 3),
    envelope_deadline TEXT
        CHECK(
            envelope_deadline IS NULL
            OR envelope_deadline LIKE '%+00:00'
            OR envelope_deadline LIKE '%Z'
        ),
    envelope_time_horizon TEXT
        CHECK(
            envelope_time_horizon IS NULL
            OR char_length(trim(envelope_time_horizon)) > 0
        ),
    project_id TEXT
        CHECK(project_id IS NULL OR char_length(trim(project_id)) > 0),
    proposed_project_name TEXT
        CHECK(
            proposed_project_name IS NULL
            OR char_length(trim(proposed_project_name)) > 0
        ),
    proposed_project_description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK(
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    ),
    approved_at TEXT
        CHECK(
            approved_at IS NULL
            OR approved_at LIKE '%+00:00'
            OR approved_at LIKE '%Z'
        ),
    approved_by TEXT
        CHECK(approved_by IS NULL OR char_length(trim(approved_by)) > 0),
    forecast_id TEXT
        CHECK(forecast_id IS NULL OR char_length(trim(forecast_id)) > 0),
    correlation_id TEXT
        CHECK(correlation_id IS NULL OR char_length(trim(correlation_id)) > 0),
    task_id TEXT CHECK(task_id IS NULL OR char_length(trim(task_id)) > 0),
    CONSTRAINT chk_charter_project_binding CHECK(
        (project_id IS NOT NULL AND proposed_project_name IS NULL)
        OR (project_id IS NULL AND proposed_project_name IS NOT NULL)
    ),
    CONSTRAINT chk_charter_approval_coupling CHECK(
        (status = 'approved'
            AND approved_at IS NOT NULL AND approved_by IS NOT NULL
            AND forecast_id IS NOT NULL AND correlation_id IS NOT NULL
            AND task_id IS NOT NULL)
        OR (status <> 'approved'
            AND approved_at IS NULL AND approved_by IS NULL
            AND forecast_id IS NULL AND correlation_id IS NULL
            AND task_id IS NULL)
    )
);

CREATE INDEX idx_project_charters_status ON project_charters(status);
CREATE INDEX idx_project_charters_project_id ON project_charters(project_id);
CREATE INDEX idx_project_charters_created_by ON project_charters(created_by);
CREATE INDEX idx_project_charters_conversation_id
    ON project_charters(conversation_id);
