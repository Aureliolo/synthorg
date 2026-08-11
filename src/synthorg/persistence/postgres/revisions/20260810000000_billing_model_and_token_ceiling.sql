-- Two things a spend ceiling could not know.
--
-- 1. Whether the money it counts measures anything.
--
-- A provider that bills by flat subscription records cost 0.0 on every
-- call. That is the correct number: there is no per-1k price to attribute.
-- What it is not is headroom, and every reader treated it as headroom
-- because nothing on the row said which of the two zeroes it was. The
-- budget page reported a full month remaining forever, every deliverable
-- receipt reported nothing spent, and the hiring signal read an
-- unmeasurable window as safe to hire against.
--
-- ``billing_model`` is stamped from the connection's own declaration at
-- ingestion, and carried on the row for the same reason ``currency`` is: a
-- connection that later changes contract must not rewrite the history of
-- what was measurable, and a connection since deleted must still be
-- answerable. ``unknown`` is the honest default for rows written before
-- anyone declared, and it reads as unmeasurable rather than as per-token:
-- assuming a ceiling binds when it may not is the failure being fixed.
--
-- 2. Anything at all, against such a provider.
--
-- The money ceiling is the only in-loop backstop a run has, and it cannot
-- fire when cost never rises. Tokens are measured on every provider,
-- billed or not, so ``tasks.hard_token_ceiling`` is the same ceiling in the
-- unit that is always available. NULL falls back to the global
-- ``budget.run_hard_token_ceiling`` setting, matching ``hard_ceiling``.

ALTER TABLE cost_records
ADD COLUMN billing_model TEXT NOT NULL DEFAULT 'unknown'
CHECK (billing_model IN ('per_token', 'flat_rate', 'unknown'));

ALTER TABLE tasks ADD COLUMN hard_token_ceiling BIGINT;
