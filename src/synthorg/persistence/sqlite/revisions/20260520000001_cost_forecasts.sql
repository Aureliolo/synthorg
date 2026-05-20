-- depends: 20260519000001_conversational_intake

-- Pre-flight cost forecasts.
--
-- One row per pre-flight estimate. The work-entry adapter consults the
-- row's ``decision`` before dispatching a brief into the work pipeline;
-- ``pending`` blocks dispatch (CostForecastApprovalRequiredError 402),
-- ``approved`` releases it, ``rejected`` terminates the work item, and
-- ``superseded`` signals that the operator edited the brief and a fresh
-- pending row took over.
--
-- ``brief_hash`` is a SHA-256 hex digest of canonical JSON of
-- (brief_text, role_skeleton, model_assignments, currency); editing
-- any of those produces a new hash. The partial-unique index on
-- ``(brief_hash) WHERE decision = 'pending'`` enforces "at most one
-- pending forecast per brief identity" without restricting historical
-- approved / rejected / superseded rows.

CREATE TABLE cost_forecasts (
    forecast_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(forecast_id)) > 0),
    brief_hash TEXT NOT NULL
        CHECK(length(trim(brief_hash)) > 0),
    estimated_cost REAL NOT NULL CHECK(estimated_cost >= 0),
    lower_bound REAL NOT NULL CHECK(lower_bound >= 0),
    upper_bound REAL NOT NULL CHECK(upper_bound >= 0),
    currency TEXT NOT NULL CHECK(length(currency) = 3),
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
        CHECK(decided_by IS NULL OR length(trim(decided_by)) > 0),
    ceiling_amount REAL
        CHECK(ceiling_amount IS NULL OR ceiling_amount >= 0),
    -- Hard-ceiling halt context. Populated when the in-loop BudgetChecker
    -- crosses the run's hard ceiling and the engine parks the context;
    -- cleared when the operator raises the ceiling so the run can resume.
    -- The forecast read path returns these so the dashboard can render a
    -- "run halted: ceiling exceeded" banner with the accumulated cost and
    -- the ceiling that was crossed, without consulting the parked-context
    -- store (which is keyed by approval id, not forecast id).
    halt_accumulated_cost REAL
        CHECK(halt_accumulated_cost IS NULL OR halt_accumulated_cost >= 0),
    halt_ceiling_amount REAL
        CHECK(halt_ceiling_amount IS NULL OR halt_ceiling_amount >= 0),
    halt_currency TEXT
        CHECK(halt_currency IS NULL OR length(halt_currency) = 3),
    halted_at TEXT
        CHECK(
            halted_at IS NULL
            OR halted_at LIKE '%+00:00'
            OR halted_at LIKE '%Z'
        ),
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
    CONSTRAINT chk_cf_halt_all_or_none CHECK(
        (halt_accumulated_cost IS NULL AND halt_ceiling_amount IS NULL
            AND halt_currency IS NULL AND halted_at IS NULL)
        OR (halt_accumulated_cost IS NOT NULL AND halt_ceiling_amount IS NOT NULL
            AND halt_currency IS NOT NULL AND halted_at IS NOT NULL)
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
