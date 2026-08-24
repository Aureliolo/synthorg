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

UPDATE settings
SET
    value = (
        -- Every branch of the CASE is load-bearing, because json_each hands
        -- back a SQL-typed value rather than the element's JSON text. Only an
        -- object or array arrives carrying its JSON subtype, so only those two
        -- survive reaggregation as themselves; a text element arrives as bare
        -- SQL text, which json_remove and json_type both reject as malformed
        -- JSON, and a boolean arrives as 1 or 0, which json_quote would rewrite
        -- into a number. One hand-edited row would otherwise take the whole
        -- revision down, which is the same failure the Postgres arm guards.
        --
        -- json() around the removal so each element aggregates back as an
        -- object rather than as a string holding an object's text. Ordered
        -- explicitly by the array index in a subquery rather than with an
        -- ORDER BY inside the aggregate: the in-aggregate form needs SQLite
        -- 3.44, and this revision runs against whatever libsqlite3 an
        -- operator's install happens to link, where a syntax error is an
        -- upgrade that stops dead. JSON() again on the way out of the
        -- subquery, because a value loses its JSON subtype crossing that
        -- boundary and JSON_GROUP_ARRAY would then aggregate every element as
        -- a STRING holding its own text.
        SELECT JSON_GROUP_ARRAY(JSON(element))
        FROM (
            SELECT
                CASE agent.type
                    WHEN 'object'
                        THEN
                            JSON(
                                JSON_REMOVE(
                                    agent.value,
                                    '$.personality',
                                    '$.personality_preset'
                                )
                            )
                    WHEN 'array' THEN JSON(agent.value)
                    WHEN 'true' THEN JSON('true')
                    WHEN 'false' THEN JSON('false')
                    ELSE JSON_QUOTE(agent.value)
                END AS element
            FROM JSON_EACH(settings.value) AS agent
            ORDER BY agent.key
        ) AS ordered_agents
    )
WHERE
    namespace = 'company'
    AND key = 'agents'
    AND JSON_VALID(value)
    AND JSON_TYPE(value) = 'array'
    AND EXISTS (
        SELECT 1
        FROM JSON_EACH(settings.value) AS agent
        WHERE
            agent.type = 'object'
            AND (
                JSON_TYPE(agent.value, '$.personality') IS NOT NULL
                OR JSON_TYPE(agent.value, '$.personality_preset') IS NOT NULL
            )
    );

-- ── 4. The identity archive's personality key ─────────────────
--
-- json_type is the existence test that survives a JSON null; json_extract
-- returns SQL NULL for both a missing key and a null value, so it cannot tell
-- them apart. The guard keeps the rewrite off rows that never carried the key,
-- which is what keeps their content_hash meaningful.

UPDATE agent_identity_versions
SET snapshot = JSON_REMOVE(snapshot, '$.personality')
WHERE
    JSON_VALID(snapshot)
    AND JSON_TYPE(snapshot, '$.personality') IS NOT NULL;
