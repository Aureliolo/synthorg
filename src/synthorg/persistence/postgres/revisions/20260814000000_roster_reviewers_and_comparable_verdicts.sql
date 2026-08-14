-- The reviewer became a roster agent, and these facts followed it.
--
-- 1. A task can now be parked because nobody holds a gate's role.
--
-- ``reviewer_unstaffed`` is a different park from ``oracle_escalated``, and
-- the difference is load-bearing: an escalation is answered by a human and
-- must not be re-judged, while an unstaffed park is answered by staffing the
-- role and MUST be re-judged once somebody holds it. Conflating them would
-- let a task that never had a review reach COMPLETED on a human decision
-- nobody was ever asked for. ``red_team_unstaffed`` is the same condition on
-- the adversarial gate, kept separate because the two name different roles
-- and the staffing sweep needs to know which one a park waits on.
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
--
-- 4. The pair an operator bound for grounding follows the key that reads it.
--
-- One setting bound the adversary AND its grounding checker. The adversary
-- is a roster agent running on its own pair, so only the grounding checker
-- still needs one, under a key that says so. The stored value is unchanged
-- in meaning, and dropping it would silently degrade grounding to the
-- heuristic on the first boot after upgrade.

ALTER TABLE tasks
DROP CONSTRAINT tasks_blocked_reason_check;

ALTER TABLE tasks
ADD CONSTRAINT tasks_blocked_reason_check CHECK (
    blocked_reason IN (
        'oracle_escalated',
        'wave_released',
        'reviewer_unstaffed',
        'red_team_unstaffed'
    )
);

ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_provider TEXT;
ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_model_id TEXT;
ALTER TABLE completion_oracle_reports
ADD COLUMN reviewer_capability TEXT CHECK (
    reviewer_capability IS NULL
    OR reviewer_capability IN ('basic', 'capable', 'expert')
);

ALTER TABLE completion_oracle_reports ALTER COLUMN reviewer_agent_id DROP NOT NULL;
ALTER TABLE completion_oracle_reports ALTER COLUMN executor_agent_id DROP NOT NULL;

-- Named ``completion_oracle_reports_check``, not ``..._executor_agent_id_check``:
-- the original was written inline on ``executor_agent_id`` but references
-- ``reviewer_agent_id`` too, and Postgres promotes a column constraint that
-- reads another column to a TABLE constraint, which takes the table's name.
ALTER TABLE completion_oracle_reports
DROP CONSTRAINT completion_oracle_reports_check;

ALTER TABLE completion_oracle_reports
ADD CONSTRAINT completion_oracle_reports_distinct_parties_check CHECK (
    reviewer_agent_id IS NULL
    OR executor_agent_id IS NULL
    OR executor_agent_id != reviewer_agent_id
);

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
ALTER TABLE red_team_reports
ADD COLUMN red_team_capability TEXT CHECK (
    red_team_capability IS NULL
    OR red_team_capability IN ('basic', 'capable', 'expert')
);

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

-- Guarded so an install that already holds the new key keeps its own value.
INSERT INTO settings (namespace, key, value, updated_at)
SELECT
    'security',
    'grounding_model',
    value,
    updated_at
FROM settings
WHERE
    namespace = 'security'
    AND key = 'red_team_model'
    AND NOT EXISTS (
        SELECT 1 FROM settings AS held
        WHERE held.namespace = 'security' AND held.key = 'grounding_model'
    );

DELETE FROM settings
WHERE namespace = 'security' AND key = 'red_team_model';
