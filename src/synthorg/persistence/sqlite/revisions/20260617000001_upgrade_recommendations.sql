-- depends: 20260615000001_audit_2335_schema

-- Persisted in-family upgrade recommendations surfaced by the periodic
-- model-refresh service. The recommendation payload + pinned agent ids
-- are JSON; status is a scalar column so the review surface can filter.
CREATE TABLE upgrade_recommendations (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    recommendation_json TEXT NOT NULL CHECK (LENGTH(TRIM(recommendation_json)) > 0),
    agent_ids_json TEXT NOT NULL CHECK (LENGTH(TRIM(agent_ids_json)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'auto_applied')
    ),
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    decided_at TEXT CHECK (
        decided_at IS NULL
        OR decided_at LIKE '%+00:00'
        OR decided_at LIKE '%Z'
    ),
    decided_by TEXT,
    -- Decision metadata is coupled to status: a pending recommendation
    -- stamps neither column; a decided one (approved / rejected /
    -- auto_applied) stamps both, with a non-blank principal.
    CHECK (
        (
            status = 'pending'
            AND decided_at IS NULL
            AND decided_by IS NULL
        )
        OR (
            status IN ('approved', 'rejected', 'auto_applied')
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND LENGTH(TRIM(decided_by)) > 0
        )
    )
);
CREATE INDEX idx_ur_status
ON upgrade_recommendations (status, created_at DESC, id DESC);
