-- depends: 20260515000001_ceremony_scheduler_state

-- WP-3 query-performance indices. No table changes: these back hot
-- read paths the 2026-05-15 audit flagged as full scans under load.
--   * org_facts_snapshot(category) WHERE retracted_at IS NULL --
--     "live facts in category X" (hot ontology read).
--   * org_facts_operation_log(operation_type) -- retract-sweep audit.
--   * approvals(risk_level, created_at DESC) and
--     approvals(action_type, created_at DESC) -- dashboard triage
--     inboxes newest-first.
--   * heartbeats(last_heartbeat_at, execution_id) -- widen the
--     single-column stale-heartbeat index so it fully covers the
--     get_stale ORDER BY without a tiebreak sort.

CREATE INDEX idx_snapshot_category_active
    ON org_facts_snapshot (category)
    WHERE retracted_at IS NULL;

CREATE INDEX idx_oplog_operation_type
    ON org_facts_operation_log (operation_type);

CREATE INDEX idx_approvals_risk_created_at
    ON approvals(risk_level, created_at DESC);

CREATE INDEX idx_approvals_action_created_at
    ON approvals(action_type, created_at DESC);

DROP INDEX idx_hb_last_heartbeat;

CREATE INDEX idx_hb_last_heartbeat
    ON heartbeats(last_heartbeat_at, execution_id);
