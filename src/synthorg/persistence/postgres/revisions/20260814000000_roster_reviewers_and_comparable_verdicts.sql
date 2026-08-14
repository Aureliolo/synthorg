-- The reviewer became a roster agent, and three facts followed it.
--
-- 1. A task can now be parked because nobody holds the reviewer role.
--
-- ``reviewer_unstaffed`` is a different park from ``oracle_escalated``, and
-- the difference is load-bearing: an escalation is answered by a human and
-- must not be re-judged, while an unstaffed park is answered by staffing the
-- role and MUST be re-judged once somebody holds it. Conflating them would
-- let a task that never had a review reach COMPLETED on a human decision
-- nobody was ever asked for.
--
-- 2. Which model produced a verdict.
--
-- Verdict quality is compared per agent AND per model, and the agent's
-- current roster binding is not evidence of what ran months ago. The three
-- reviewer columns are nullable because rows written before this change
-- genuinely do not know; NULL is the honest value, never a stand-in.
--
-- 3. Who attacked a deliverable, and who wrote it.
--
-- ``red_team_reports`` recorded neither, so the red-team gate had no
-- structural no-self-attack guard at all: its twin
-- ``completion_oracle_reports`` has carried ``CHECK (executor <> reviewer)``
-- since it was created. Both id columns are nullable for the same reason as
-- above (historical rows cannot name either party), and the CHECK therefore
-- guards the both-present case, which is every row written from now on.
--
-- The table also moves to a surrogate ``report_id``, exactly as
-- ``completion_oracle_reports`` did in 20260813000000 and for the same
-- reason: the gate runs again whenever a task is decided, re-opened and
-- decided again, and an ``execution_id`` primary key made the second
-- (superseding) verdict collide and be swallowed while the stale row stayed
-- the only durable record.

ALTER TABLE tasks
DROP CONSTRAINT tasks_blocked_reason_check;

ALTER TABLE tasks
ADD CONSTRAINT tasks_blocked_reason_check CHECK (
    blocked_reason IN ('oracle_escalated', 'wave_released', 'reviewer_unstaffed')
);

ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_provider TEXT;
ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_model_id TEXT;
ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_capability TEXT;

CREATE INDEX idx_cor_reviewer_agent_id
ON completion_oracle_reports (reviewer_agent_id, recorded_at DESC);

ALTER TABLE red_team_reports
DROP CONSTRAINT red_team_reports_pkey;

ALTER TABLE red_team_reports
ADD COLUMN report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY;

ALTER TABLE red_team_reports ADD COLUMN red_team_agent_id TEXT;
ALTER TABLE red_team_reports ADD COLUMN executor_agent_id TEXT;
ALTER TABLE red_team_reports ADD COLUMN red_team_provider TEXT;
ALTER TABLE red_team_reports ADD COLUMN red_team_model_id TEXT;
ALTER TABLE red_team_reports ADD COLUMN red_team_capability TEXT;

ALTER TABLE red_team_reports
ADD CONSTRAINT red_team_reports_distinct_parties_check CHECK (
    red_team_agent_id IS NULL
    OR executor_agent_id IS NULL
    OR executor_agent_id != red_team_agent_id
);

CREATE INDEX idx_rtr_execution_id
ON red_team_reports (execution_id, recorded_at DESC);
CREATE INDEX idx_rtr_red_team_agent_id
ON red_team_reports (red_team_agent_id, recorded_at DESC);
