-- Widen upgrade_recommendations.status to accept 'superseded': a reconcile
-- pass retires a pending recommendation it no longer produces (the current
-- model was removed, or the recommender's newest-in-family pick changed) so a
-- stale row never lingers on the review surface. Like a human decision, a
-- superseded row stamps decided_at / decided_by (the system 'reconcile'
-- actor), so it moves through the decided branch of the coupling CHECK.
--
-- SQLite cannot ALTER an existing CHECK constraint, so the table is rebuilt
-- (create-new, copy, drop, rename) and its status index recreated. No other
-- table references upgrade_recommendations, so the drop/rename fires no
-- cascades.

CREATE TABLE upgrade_recommendations_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    recommendation_json TEXT NOT NULL CHECK (LENGTH(TRIM(recommendation_json)) > 0),
    agent_ids_json TEXT NOT NULL CHECK (LENGTH(TRIM(agent_ids_json)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'auto_applied', 'superseded')
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
    -- auto_applied) or one retired by a reconcile pass (superseded) stamps
    -- both, with a non-blank principal (the system actor for superseded).
    CHECK (
        (
            status = 'pending'
            AND decided_at IS NULL
            AND decided_by IS NULL
        )
        OR (
            status IN ('approved', 'rejected', 'auto_applied', 'superseded')
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND LENGTH(TRIM(decided_by)) > 0
        )
    )
);

INSERT INTO upgrade_recommendations_new (
    id,
    recommendation_json,
    agent_ids_json,
    status,
    created_at,
    decided_at,
    decided_by
)
SELECT
    id,
    recommendation_json,
    agent_ids_json,
    status,
    created_at,
    decided_at,
    decided_by
FROM upgrade_recommendations;

DROP TABLE upgrade_recommendations;

ALTER TABLE upgrade_recommendations_new RENAME TO upgrade_recommendations;

CREATE INDEX idx_ur_status
ON upgrade_recommendations (status, created_at DESC, id DESC);
