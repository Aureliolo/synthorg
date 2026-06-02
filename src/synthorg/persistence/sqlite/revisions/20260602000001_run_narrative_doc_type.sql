-- depends: 20260531000001_conversational_org_interface

-- Widen the project_docs.doc_type CHECK to
-- admit 'run_narrative' (the Chief-of-Staff run narrative) and
-- 'codebase_analysis' (the DocType member shipped without a matching
-- CHECK entry, so a brownfield-intake write would have failed at
-- insert). SQLite cannot ALTER a CHECK constraint, so the table is
-- rebuilt (new + copy + drop + rename); the PRIMARY KEY, the FK cascade
-- from projects, and all four indexes are recreated unchanged.

CREATE TABLE project_docs_new (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    doc_type TEXT NOT NULL
    CHECK (
        doc_type IN (
            'status_report',
            'deliverable',
            'knowledge_note',
            'codebase_analysis',
            'run_narrative'
        )
    ),
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    head_commit_sha TEXT NOT NULL,
    last_indexed_commit_sha TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

INSERT INTO project_docs_new (
    project_id, slug, doc_type, title, tags,
    head_commit_sha, last_indexed_commit_sha, created_at, updated_at
)
SELECT
    project_id,
    slug,
    doc_type,
    title,
    tags,
    head_commit_sha,
    last_indexed_commit_sha,
    created_at,
    updated_at
FROM project_docs;

DROP TABLE project_docs;
ALTER TABLE project_docs_new RENAME TO project_docs;

CREATE INDEX idx_project_docs_updated_at
ON project_docs (updated_at DESC);

CREATE INDEX idx_project_docs_project_recent
ON project_docs (project_id, updated_at DESC, slug DESC);

CREATE INDEX idx_project_docs_doc_type
ON project_docs (project_id, doc_type);

CREATE INDEX idx_project_docs_reindex
ON project_docs (project_id)
WHERE
    last_indexed_commit_sha IS NULL
    OR last_indexed_commit_sha != head_commit_sha;
