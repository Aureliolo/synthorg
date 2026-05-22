-- Flight-recorder frames: per-turn cockpit replay records (append-only).
CREATE TABLE flight_recorder_frames (
    id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    task_id TEXT,
    agent_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL CHECK (turn_index >= 1),
    timestamp TIMESTAMPTZ NOT NULL,
    prompt_summary TEXT,
    response_summary TEXT,
    decision TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cost NUMERIC(12, 6) NOT NULL DEFAULT 0.0 CHECK (cost >= 0),
    status TEXT NOT NULL,
    intervention_kind TEXT
);

CREATE INDEX idx_frf_execution_turn
    ON flight_recorder_frames(execution_id, turn_index);
CREATE INDEX idx_frf_task_id ON flight_recorder_frames(task_id);
CREATE INDEX idx_frf_agent_id ON flight_recorder_frames(agent_id);
CREATE INDEX idx_frf_timestamp ON flight_recorder_frames(timestamp);
