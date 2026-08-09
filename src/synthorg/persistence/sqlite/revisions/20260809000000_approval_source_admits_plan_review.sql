-- The approvals table refused the one source the plan-review gate writes.
--
-- ``ApprovalSource`` has five members; the ``source`` CHECK admitted four.
-- ``plan_review`` was never in the list, so every plan reaching human
-- review failed to persist its approval. The gap was invisible for as long
-- as it existed because no approval reached the database at all: the store
-- was constructed without a repository, held its queue in memory, and the
-- CHECK was never evaluated. The first run with durable approvals hit it
-- immediately, and the plan was failed nine milliseconds after reaching
-- PENDING_REVIEW, with no approval for an operator to decide.
--
-- SQLite cannot ALTER an existing CHECK constraint, so the table is rebuilt
-- (create-new, copy, drop, rename) and every index recreated.
-- ``fk_approvals_task_id`` is carried over unchanged; no table references
-- approvals by foreign key (``conversations.approval_id`` and
-- ``parked_contexts.approval_id`` are deliberately plain TEXT), so the
-- drop/rename fires no cascades.
--
-- ``check_sql_enum_check_constraints.py`` now holds every such list to its
-- Python enum, so the next member added to ``ApprovalSource`` fails the
-- gate rather than the run.

CREATE TABLE approvals_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    action_type TEXT NOT NULL CHECK (LENGTH(TRIM(action_type)) > 0),
    title TEXT NOT NULL CHECK (LENGTH(TRIM(title)) > 0),
    description TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK (LENGTH(TRIM(requested_by)) > 0),
    risk_level TEXT NOT NULL DEFAULT 'medium' CHECK (
        risk_level IN ('low', 'medium', 'high', 'critical')
    ),
    source TEXT NOT NULL DEFAULT 'review_gate' CHECK (
        source IN (
            'parked_context', 'review_gate',
            'conversational_intake', 'conversational_invite',
            'plan_review'
        )
    ),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    expires_at TEXT CHECK (
        expires_at IS NULL OR expires_at LIKE '%+00:00' OR expires_at LIKE '%Z'
    ),
    decided_at TEXT CHECK (
        decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'
    ),
    decided_by TEXT,
    decision_reason TEXT,
    task_id TEXT CONSTRAINT fk_approvals_task_id REFERENCES tasks (id),
    evidence_package TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR consumed_at LIKE '%+00:00' OR consumed_at LIKE '%Z'
    ),
    CHECK (
        (decided_at IS NULL AND decided_by IS NULL)
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK (
        status != 'rejected' OR (decision_reason IS NOT NULL AND LENGTH(TRIM(decision_reason)) > 0)
    )
);

INSERT INTO approvals_new (
    id,
    action_type,
    title,
    description,
    requested_by,
    risk_level,
    source,
    status,
    created_at,
    expires_at,
    decided_at,
    decided_by,
    decision_reason,
    task_id,
    evidence_package,
    metadata,
    consumed_at
)
SELECT
    id,
    action_type,
    title,
    description,
    requested_by,
    risk_level,
    source,
    status,
    created_at,
    expires_at,
    decided_at,
    decided_by,
    decision_reason,
    task_id,
    evidence_package,
    metadata,
    consumed_at
FROM approvals;

DROP TABLE approvals;

ALTER TABLE approvals_new RENAME TO approvals;

CREATE INDEX idx_approvals_status ON approvals (status);
CREATE INDEX idx_approvals_action_type ON approvals (action_type);
CREATE INDEX idx_approvals_risk_level ON approvals (risk_level);
CREATE INDEX idx_approvals_requested_by_status ON approvals (requested_by, status);
CREATE INDEX idx_approvals_status_expires_at ON approvals (status, expires_at);
CREATE INDEX idx_approvals_task_id ON approvals (task_id);
CREATE INDEX idx_approvals_status_created_at
ON approvals (status, created_at DESC);
CREATE INDEX idx_approvals_risk_created_at
ON approvals (risk_level, created_at DESC);
CREATE INDEX idx_approvals_action_created_at
ON approvals (action_type, created_at DESC);
