-- Runtime tool-call failure signals: the time-decayed per-(provider, model)
-- accumulator for the runtime tool-call feedback loop. decayed_at is epoch
-- seconds (the same decay-arithmetic float representation as
-- circuit_breaker_state.opened_at), so an observation can decay the score
-- forward across process restarts without an ISO round-trip.

CREATE TABLE model_tool_call_signals (
    provider_name TEXT NOT NULL CHECK (LENGTH(provider_name) > 0),
    model_id TEXT NOT NULL CHECK (LENGTH(model_id) > 0),
    failure_score REAL NOT NULL DEFAULT 0 CHECK (failure_score >= 0),
    decayed_at REAL NOT NULL CHECK (decayed_at >= 0),
    PRIMARY KEY (provider_name, model_id)
);
