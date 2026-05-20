-- depends: 20260519000001_project_workspaces

-- Living-documentation metadata (#1976). One row per (project, slug)
-- carrying pointers to the doc bytes (which live in the project git
-- workspace at <workspace>/.synthorg/docs/<doc_type>/<slug>.json) and
-- the indexing state needed to replay unindexed commits on boot.
-- ON DELETE CASCADE: deleting a project drops its doc metadata; the
-- on-disk JSON is reclaimed when the workspace itself is reclaimed.

CREATE TABLE project_docs (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    doc_type TEXT NOT NULL
        CHECK (doc_type IN ('status_report', 'deliverable', 'knowledge_note')),
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    head_commit_sha TEXT NOT NULL,
    last_indexed_commit_sha TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (project_id, slug),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_project_docs_updated_at
    ON project_docs(updated_at DESC);

CREATE INDEX idx_project_docs_doc_type
    ON project_docs(project_id, doc_type);

CREATE INDEX idx_project_docs_reindex
    ON project_docs(project_id)
    WHERE last_indexed_commit_sha IS NULL
       OR last_indexed_commit_sha <> head_commit_sha;
