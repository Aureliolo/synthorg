-- depends: 20260519000001_conversational_intake

-- Pre-flight cost forecasts.
--
-- See ``synthorg/persistence/sqlite/revisions/20260520000001_cost_forecasts.sql``
-- for the design notes on brief identity, the decision state machine,
-- and the unique-pending invariant.

CREATE TABLE cost_forecasts (
    forecast_id TEXT NOT NULL PRIMARY KEY
        CHECK(char_length(trim(forecast_id)) > 0),
    brief_hash TEXT NOT NULL
        CHECK(char_length(trim(brief_hash)) > 0),
    estimated_cost DOUBLE PRECISION NOT NULL CHECK(estimated_cost >= 0),
    lower_bound DOUBLE PRECISION NOT NULL CHECK(lower_bound >= 0),
    upper_bound DOUBLE PRECISION NOT NULL CHECK(upper_bound >= 0),
    currency TEXT NOT NULL CHECK(char_length(currency) = 3),
    decision TEXT NOT NULL DEFAULT 'pending' CHECK(
        decision IN ('pending', 'approved', 'rejected', 'superseded')
    ),
    decided_at TEXT
        CHECK(
            decided_at IS NULL
            OR decided_at LIKE '%+00:00'
            OR decided_at LIKE '%Z'
        ),
    decided_by TEXT
        CHECK(decided_by IS NULL OR char_length(trim(decided_by)) > 0),
    ceiling_amount DOUBLE PRECISION
        CHECK(ceiling_amount IS NULL OR ceiling_amount >= 0),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK(
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    ),
    CONSTRAINT chk_cf_lower_le_upper CHECK(lower_bound <= upper_bound),
    CONSTRAINT chk_cf_estimate_within_band CHECK(
        estimated_cost >= lower_bound AND estimated_cost <= upper_bound
    ),
    CONSTRAINT chk_cf_decision_timestamp CHECK(
        (decision = 'pending' AND decided_at IS NULL AND decided_by IS NULL)
        OR (decision = 'superseded' AND decided_at IS NOT NULL AND decided_by IS NULL)
        OR (decision IN ('approved', 'rejected')
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL)
    )
);

-- Partial unique index: at most one pending row per brief_hash.
CREATE UNIQUE INDEX idx_cost_forecasts_unique_pending
    ON cost_forecasts(brief_hash)
    WHERE decision = 'pending';

CREATE INDEX idx_cost_forecasts_brief_hash
    ON cost_forecasts(brief_hash);

CREATE INDEX idx_cost_forecasts_decision
    ON cost_forecasts(decision);
