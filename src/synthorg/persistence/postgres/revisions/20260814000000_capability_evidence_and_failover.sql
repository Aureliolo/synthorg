-- What the loop needs to grade a model on evidence, and to answer
-- afterwards which connection actually served a request.
--
-- 1. Externally-sourced capability evidence gets somewhere to live.
--
-- One row per (source_label, model_identifier, axis): what one published
-- source measured about one model. ``model_identifier`` is the source's own
-- string, kept verbatim so an unresolved row stays inspectable rather than
-- vanishing into a failed match. ``as_of`` is when the SOURCE measured it
-- and is what staleness is read from; ``ingested_at`` is when we read the
-- source. A refresh upserts and never bulk-deletes, so a feed that drops a
-- model or fails outright leaves its last good row ageing visibly rather
-- than silently un-grading the model.
--
-- 2. Each source records whether it is still answering.
--
-- The scores say what a source measured; this says whether the source still
-- works. A feed that has been failing for a month still has last month's
-- rows in the table, and without this record the grading built on them
-- looks exactly as healthy as one refreshed an hour ago.
-- ``last_attempted_at`` is what the age gate reads, so a broken feed retries
-- on the same cadence as a working one rather than on every request.
--
-- 3. A system feature served by its operator-declared alternate says so.
--
-- The event log says a failover happened; this survives the restart. An
-- operator reading a cost row, a latency spike or an odd answer a week later
-- needs to know which connection served that request, and the setting only
-- says which one was allowed to. Both pairs are recorded in full, because
-- "the alternate" is not an answer once the route map has been edited.

CREATE TABLE model_capability_scores (
    source_label TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(source_label)) > 0),
    model_identifier TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(model_identifier)) > 0),
    axis TEXT NOT NULL CHECK (axis IN ('coding', 'reasoning', 'general')),
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 100),
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_label, model_identifier, axis)
);

CREATE INDEX idx_model_capability_scores_model
ON model_capability_scores (model_identifier, axis);
CREATE INDEX idx_model_capability_scores_source
ON model_capability_scores (source_label, as_of DESC);

CREATE TABLE capability_source_statuses (
    source_label TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(source_label)) > 0),
    last_attempted_at TIMESTAMPTZ,
    last_succeeded_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    rows_read INTEGER NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    rows_skipped INTEGER NOT NULL DEFAULT 0 CHECK (rows_skipped >= 0),
    scores_written INTEGER NOT NULL DEFAULT 0 CHECK (scores_written >= 0),
    feed_url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE provider_failover_events (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    feature TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(feature)) > 0),
    declared_provider TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(declared_provider)) > 0),
    declared_model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(declared_model)) > 0),
    served_provider TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(served_provider)) > 0),
    served_model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(served_model)) > 0),
    trigger_class TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(trigger_class)) > 0),
    trigger_stage TEXT NOT NULL CHECK (trigger_stage IN ('preflight', 'retry')),
    agent_id TEXT,
    task_id TEXT
);

CREATE INDEX idx_provider_failover_events_occurred
ON provider_failover_events (occurred_at DESC);
CREATE INDEX idx_provider_failover_events_feature
ON provider_failover_events (feature, occurred_at DESC);
CREATE INDEX idx_provider_failover_events_declared_provider
ON provider_failover_events (declared_provider, occurred_at DESC);
