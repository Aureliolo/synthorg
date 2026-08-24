-- Retire the personality surface: the preset store, its settings, and the
-- keys it left inside stored rosters and identity snapshots.
--
-- An agent is a bound (role, model) unit. The middle member of the old triple
-- had no producer of evidence: the Big Five scorer in core/personality.py had
-- no caller at all, and the half that reached a prompt is persona injection,
-- which steers what a model says about itself and not what it does.
--
-- Five of the six statements here rewrite ROWS rather than shapes, and the
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
-- Four tables carry a serialised identity, not two. personality was declared
-- with a default_factory, so it is present in EVERY dump written before this
-- revision rather than only in the ones an operator customised.
--
-- content_hash on the archive is deliberately left alone. It is a dedupe key,
-- not an integrity check (get_by_content_hash is a lookup and no reader
-- verifies it), and it is computed in Python over the model dump, so SQL
-- cannot recompute it correctly. A stale hash costs one redundant version row
-- the next time that identity is saved; a hash rewritten to something wrong
-- would cost more.
--
-- This arm is not byte-identical to the SQLite one and is not meant to be.
-- JSONB_AGG(...)::TEXT emits jsonb's canonical form, which reorders object
-- keys, collapses duplicates and normalises numbers; SQLite emits minified
-- text in the original key order. Nothing observable diverges, because every
-- reader parses before comparing and the company snapshot's ETag hashes the
-- PARSED models rather than the stored text.

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
-- clause left to right, so neither the namespace/key equality nor an earlier
-- AND-term stops `value::JSONB` being attempted on the Fernet blob under
-- providers.configs or on a bare string like `log_only`; a CASE is the
-- documented construct that does.
--
-- The CASE tests `value IS JSON ARRAY` rather than the namespace and key,
-- because that predicate is TOTAL: it answers false for every unparseable
-- value instead of raising, which the `JSONB_TYPEOF(value::JSONB)` it replaces
-- did not. Gating on the row's identity only moved the problem, since this
-- statement's whole purpose is to touch that row, so a hand-edited or
-- truncated roster still reached the cast and took the upgrade down with a
-- 22P02 naming a cast rather than a row. The SQLite arm skips such a row.
--
-- `jsonb - text` raises on a scalar operand, so the removal is applied per
-- element rather than to whatever the array happens to contain: one
-- hand-edited non-object element beside one well-formed agent would otherwise
-- roll back this whole revision. The SQLite arm gets that for free, since
-- JSON_REMOVE returns a scalar unchanged.
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
        WHEN value IS JSON ARRAY
            THEN EXISTS (
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
--
-- The typeof guard is not redundant with it. `jsonb ? text` is a containment
-- test, not a key test: on the scalar '"personality"' it answers true, and
-- `snapshot - 'personality'` then raises "cannot delete from scalar" and rolls
-- the revision back. The column is JSONB NOT NULL with no object CHECK, so
-- nothing else refuses a scalar. Step 3 above guards per element for the same
-- reason; SQLite needs neither, since JSON_TYPE with a path is a true key test.

UPDATE agent_identity_versions
SET snapshot = snapshot - 'personality'
WHERE
    JSONB_TYPEOF(snapshot) = 'object'
    AND JSONB_EXISTS(snapshot, 'personality');

-- ── 5. In-flight execution contexts ───────────────────────────
--
-- AgentContext embeds the whole AgentIdentity, and both of these tables store
-- the context verbatim as AgentContext.model_dump_json(). They outlive a
-- restart on purpose, which is exactly what puts them on the far side of an
-- upgrade: a parked context waits for a human under the shipped SUPERVISED
-- default with no deadline, and a checkpoint is what crash recovery replays.
--
-- Leaving them would not degrade, it would strand. Both read paths validate
-- into AgentContext and raise, and the parked reader preserves the row after
-- the failure, so a resume that cannot parse fails identically on every retry
-- with the approval already decided.
--
-- `#-` takes a path, which is what reaches the key under $.identity, and it
-- raises on a scalar exactly as `-` does, so both levels are typed first.

UPDATE checkpoints
SET context_json = context_json #- '{identity,personality}'
WHERE
    JSONB_TYPEOF(context_json) = 'object'
    AND JSONB_TYPEOF(context_json -> 'identity') = 'object'
    AND JSONB_EXISTS(context_json -> 'identity', 'personality');

UPDATE parked_contexts
SET context_json = context_json #- '{identity,personality}'
WHERE
    JSONB_TYPEOF(context_json) = 'object'
    AND JSONB_TYPEOF(context_json -> 'identity') = 'object'
    AND JSONB_EXISTS(context_json -> 'identity', 'personality');
