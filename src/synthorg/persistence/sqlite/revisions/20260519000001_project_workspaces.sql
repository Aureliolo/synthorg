-- depends: 20260518000001_approval_source

-- Persistent per-project workspace mapping (#1974). One row per project
-- (1:1), recording where the git-backed working tree lives on the
-- persistent volume and which backend provisioned it, so a session
-- restart re-locates the same directory and a configured backend switch
-- is detectable against the persisted kind. ON DELETE CASCADE: deleting
-- a project drops its workspace mapping (the on-disk tree is reclaimed
-- separately by the workspace service).

CREATE TABLE project_workspaces (
    project_id TEXT NOT NULL PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    UNIQUE (workspace_path),
    git_backend_kind TEXT NOT NULL
        CHECK (git_backend_kind IN ('embedded', 'external_remote', 'local_path')),
    remote_ref TEXT,
    default_branch TEXT NOT NULL DEFAULT 'main',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_project_workspaces_created_at
    ON project_workspaces(created_at);
