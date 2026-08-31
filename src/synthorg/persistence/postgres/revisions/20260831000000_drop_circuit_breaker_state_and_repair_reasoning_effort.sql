-- circuit_breaker_state's sole writer was DelegationCircuitBreaker, part of
-- the communication.loop_prevention package. Delegation loop protection is
-- now the ancestry + depth guard in engine/delegation/, which reads the
-- parent-task chain rather than persisting per-pair state, so nothing
-- writes or reads this table any more.

DROP TABLE IF EXISTS circuit_breaker_state;

-- engine.reasoning_effort_low's registered default moves from 'none' to
-- 'low', because the absence of a request is not a depth: it leaves the
-- model at whatever its provider defaults to (medium to high on current
-- families), which is the OPPOSITE of the cost discipline the posture
-- promises at its highest-volume stakes level. A deployment that already
-- ran the cost_disciplined posture has both rows persisted at 'none' (the
-- posture wrote reasoning_effort_low there too, a no-op against the OLD
-- default that becomes a pinned override once the default changes), which
-- would otherwise strand that deployment on the old behaviour forever and
-- risk an inverted reasoning-effort ladder once reasoning_effort_low
-- inherits the new, higher-ranked default while reasoning_effort_normal
-- stays behind at 'none'.
--
-- reasoning_effort_low: delete the row so it inherits the new 'low'
-- default, restoring the cheap, predictable behaviour this migration
-- exists to ship.
DELETE FROM settings
WHERE
    namespace = 'engine'
    AND key = 'reasoning_effort_low'
    AND value = 'none';

-- reasoning_effort_normal: rewrite to 'low', its own registered default,
-- which the posture's write had overridden to 'none'.
UPDATE settings
SET value = 'low'
WHERE
    namespace = 'engine'
    AND key = 'reasoning_effort_normal'
    AND value = 'none';
