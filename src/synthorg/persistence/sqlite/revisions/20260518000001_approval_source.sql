-- depends: 20260517000001_oauth_state_nonce 20260517000001_wp3_query_indices

-- Persisted approval-origin discriminator. Routing of a decided
-- approval (mid-execution parked-context resume vs. review-gate
-- transition) keys off this column rather than a live parked-context
-- probe, so the flow is deterministic even when the parked-context
-- backend is momentarily unavailable. Existing rows default to
-- 'review_gate' (the safe, non-resuming path).

ALTER TABLE approvals ADD COLUMN source TEXT NOT NULL DEFAULT 'review_gate'
    CHECK (source IN ('parked_context', 'review_gate'));
