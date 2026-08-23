-- Drop the meeting / ceremony / conflict-resolution stack's tables and the
-- settings rows its keys left behind.
--
-- The three tables belonged to one stack an operator had to start by hand and
-- that nothing in the orchestration loop ever entered: a sprint's ceremonies
-- fired meetings, a structured-phases meeting detected disagreement, and the
-- disagreement reached a resolution service. ``conflict_escalations`` had a
-- single producer, the human-escalation resolver at the end of that chain, and
-- human decisions are already served by the approval store, which plan review,
-- initiative stall, org hire and the review gates all use.
-- ``ceremony_scheduler_state`` held per-sprint trigger counters, and
-- ``meeting_cooldown`` one row per recurring meeting type so a restart could
-- not re-fire a meeting inside its window. All three lose both writer and
-- reader with the stack.
--
-- ``collaboration_metrics.meeting_contribution`` scored how well an agent
-- contributed to a discussion. The dimension survives (the group chat is the
-- live multi-party surface) but its name pointed at a subsystem that no longer
-- exists, so the column is renamed rather than dropped and the recorded values
-- carry over.
--
-- The indexes go with their table on SQLite, so only the DROP is needed. The
-- settings rows are removed in the same revision: a key whose definition no
-- longer exists is a row the resolver refuses on the next boot.

DROP INDEX IF EXISTS idx_conflict_escalations_unique_pending_conflict;
DROP INDEX IF EXISTS idx_conflict_escalations_status_expires_at;
DROP INDEX IF EXISTS idx_conflict_escalations_conflict_id;
DROP INDEX IF EXISTS idx_conflict_escalations_status_created;
DROP TABLE IF EXISTS conflict_escalations;

DROP TABLE IF EXISTS ceremony_scheduler_state;

DROP TABLE IF EXISTS meeting_cooldown;

ALTER TABLE collaboration_metrics
RENAME COLUMN meeting_contribution TO discussion_contribution;

DELETE FROM settings
WHERE
    (
        namespace = 'communication'
        AND key IN (
            'conflict_judge_model',
            'meeting_conflict_escalation_enabled',
            'escalation_sweeper_paused',
            'escalation_subscriber_reconnect_delay_seconds',
            'meetings_enabled',
            'escalation_notify_subscriber_enabled',
            'webhook_bridge_enabled',
            'webhook_bridge_poll_timeout_seconds',
            'webhook_bridge_max_consecutive_errors'
        )
    )
    OR (
        namespace = 'coordination'
        AND key IN (
            'ceremony_strategy',
            'ceremony_strategy_config',
            'ceremony_velocity_calculator',
            'ceremony_auto_transition',
            'ceremony_transition_threshold',
            'dept_ceremony_policies',
            'department_policy_cas_retry_attempts'
        )
    )
    OR (
        namespace = 'strategy'
        AND key IN (
            'consensus_velocity_action',
            'consensus_velocity_threshold',
            'premortem_participants'
        )
    )
    OR (
        namespace = 'api'
        AND key IN (
            'max_meeting_context_keys',
            'lifecycle_meeting_scheduler_shutdown_seconds'
        )
    );
