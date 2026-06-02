-- depends: 20260531000001_conversational_org_interface

-- Deliverable receipts (provenance bundles) plus the two capture tables
-- they aggregate from. ``deliverable_receipt`` is keyed by a surrogate
-- ``receipt_id`` with a UNIQUE constraint on ``task_id`` (one current
-- receipt per task; rebuilds upsert on the conflict). The full receipt
-- is stored as JSON in ``payload_json``; the structured columns exist
-- for filtering. ``knowledge_usage_record`` and ``code_execution_record``
-- are append-only capture logs written during a run and keyed by
-- ``execution_id`` so the receipt builder can reconstruct sources and
-- test results for that run.

CREATE TABLE deliverable_receipt (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    deliverable_doc_slug TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    total_cost REAL NOT NULL DEFAULT 0.0 CHECK (total_cost >= 0),
    currency TEXT NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_deliverable_receipt_task
ON deliverable_receipt (task_id);
CREATE INDEX idx_deliverable_receipt_project
ON deliverable_receipt (project_id, issued_at DESC);
CREATE INDEX idx_deliverable_receipt_slug
ON deliverable_receipt (deliverable_doc_slug);

CREATE TABLE knowledge_usage_record (
    record_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX idx_knowledge_usage_execution
ON knowledge_usage_record (execution_id, recorded_at DESC);
CREATE INDEX idx_knowledge_usage_task
ON knowledge_usage_record (task_id);
CREATE INDEX idx_knowledge_usage_project
ON knowledge_usage_record (project_id, recorded_at DESC);
CREATE INDEX idx_knowledge_usage_source
ON knowledge_usage_record (source_id);

CREATE TABLE code_execution_record (
    record_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('general', 'tests')),
    command TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    timed_out INTEGER NOT NULL CHECK (timed_out IN (0, 1)),
    stdout_tail TEXT,
    stderr_tail TEXT,
    executed_at TEXT NOT NULL,
    CHECK (passed = (returncode = 0 AND timed_out = 0))
);

CREATE INDEX idx_code_execution_execution
ON code_execution_record (execution_id, executed_at DESC);
CREATE INDEX idx_code_execution_task
ON code_execution_record (task_id);
CREATE INDEX idx_code_execution_project
ON code_execution_record (project_id, executed_at DESC);
