-- Retire the personality surface: the preset store, its settings, and the
-- keys it left inside stored rosters and identity snapshots.
--
-- An agent is a bound (role, model) unit. The middle member of the old triple
-- had no producer of evidence: the Big Five scorer in core/personality.py had
-- no caller at all, and the half that reached a prompt is persona injection,
-- which steers what a model says about itself and not what it does.
--
-- Three of the four statements here rewrite ROWS rather than shapes, and the
-- schema-drift gate cannot see any of them: it builds both schemas from empty
-- and compares, and only custom_presets changes shape. AgentConfig and
-- AgentIdentity are both extra="forbid", so a stored key the model no longer
-- declares fails validation on read. company.agents is ONE row holding the
-- whole roster, so a single stale key reads the company as empty behind one
-- warning:
--
--   persistence.settings.fetch_failed  namespace=company key=agents
--     reason=schema_drift
--     error="Extra inputs are not permitted [personality]"
--
-- content_hash on the archive is deliberately left alone. It is a dedupe key,
-- not an integrity check (get_by_content_hash is a lookup and no reader
-- verifies it), and it is computed in Python over the model dump, so SQL
-- cannot recompute it correctly. A stale hash costs one redundant version row
-- the next time that identity is saved; a hash rewritten to something wrong
-- would cost more.

-- ── 1. The custom preset store ────────────────────────────────
--
-- Operator-authored personality presets, merged with the 24 built-ins behind
-- the preset service. Both ends are gone: nothing writes a preset and nothing
-- resolves one onto an agent.

DROP TABLE IF EXISTS custom_presets;

-- ── 2. The trimming knobs ─────────────────────────────────────
--
-- These bounded the personality section of the system prompt. A read is gated
-- on the settings registry, so an orphan row is unreachable rather than
-- harmful; it is deleted so an operator browsing the table is not shown a
-- value nothing will ever apply.

DELETE FROM settings
WHERE
    namespace = 'engine'
    AND key IN (
        'personality_trimming_enabled',
        'personality_max_tokens_override',
        'personality_trimming_notify'
    );

-- ── 3. The roster's personality keys ──────────────────────────
--
-- Two guards, because `settings` is one table holding every namespace's values
-- and only this row is JSON. Postgres does not promise to evaluate a WHERE
-- clause left to right, so the namespace/key equality alone does not stop
-- `value::JSONB` being attempted on the Fernet blob under providers.configs or
-- on a bare string like `log_only`; a CASE is the documented construct that
-- does. And `jsonb - text` raises on a scalar operand, so the removal is
-- applied per element rather than to whatever the array happens to contain:
-- one hand-edited non-object element beside one well-formed agent would
-- otherwise roll back this whole revision. The SQLite arm gets both properties
-- for free, since JSON_REMOVE returns a scalar unchanged.
UPDATE settings
SET
    value = (
        SELECT
            JSONB_AGG(
                CASE
                    WHEN JSONB_TYPEOF(agent) = 'object'
                        THEN agent - 'personality' - 'personality_preset'
                    ELSE agent
                END
                ORDER BY ordinality
            )::TEXT
        -- WITH ORDINALITY needs the table alias to name both output columns,
        -- which is what makes `agent` and `ordinality` resolvable above; the
        -- linter reads `t` as unused because nothing qualifies with it.
        FROM JSONB_ARRAY_ELEMENTS(value::JSONB) WITH ORDINALITY AS t (agent, ordinality)  -- noqa: AL05
    )
WHERE
    namespace = 'company'
    AND key = 'agents'
    AND CASE
        WHEN namespace = 'company' AND key = 'agents'
            THEN
                JSONB_TYPEOF(value::JSONB) = 'array'
                AND EXISTS (
                    SELECT 1
                    FROM JSONB_ARRAY_ELEMENTS(value::JSONB) AS agent
                    WHERE
                        JSONB_TYPEOF(agent) = 'object'
                        AND (
                            JSONB_EXISTS(agent, 'personality')
                            OR JSONB_EXISTS(agent, 'personality_preset')
                        )
                )
        ELSE FALSE
    END;

-- ── 4. The identity archive's personality key ─────────────────
--
-- JSONB_EXISTS is the existence test that survives a JSON null, and the guard
-- keeps the rewrite off rows that never carried the key, which is what keeps
-- their content_hash meaningful.

UPDATE agent_identity_versions
SET snapshot = snapshot - 'personality'
WHERE JSONB_EXISTS(snapshot, 'personality');
