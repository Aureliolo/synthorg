-- depends: 20260521000001_external_api_governed_access

-- Self-extending toolkit: authored tool blueprints.
--
-- dynamic_tools stores runtime-authored MCP tools. A blueprint is a
-- declarative spec (name, capability, JSON Schema) plus a sandbox
-- script body, governed through the TOOL_CREATION proposal altitude.
-- state drives the lifecycle: pending (proposed) -> validated (passed
-- the benchmark gate) -> active (live-registered) -> retired (rolled
-- back). The state-correlated timestamps are stamped on transition.
-- parameters_schema and validation are stored as JSON text (TEXT here,
-- JSONB on Postgres); a real Pydantic args_model is materialised from
-- parameters_schema at registration time.

CREATE TABLE dynamic_tools (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(id)) > 0),
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    capability TEXT NOT NULL CHECK (length(trim(capability)) > 0),
    parameters_schema TEXT NOT NULL,
    script_body TEXT NOT NULL CHECK (length(trim(script_body)) > 0),
    sandbox_backend TEXT NOT NULL
        CHECK (sandbox_backend IN ('docker', 'subprocess')),
    requires_network INTEGER NOT NULL DEFAULT 0
        CHECK (requires_network IN (0, 1)),
    action_type TEXT NOT NULL CHECK (length(trim(action_type)) > 0),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'validated', 'active', 'retired')),
    created_at TEXT NOT NULL
        CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    validated_at TEXT
        CHECK (validated_at IS NULL
            OR validated_at LIKE '%+00:00' OR validated_at LIKE '%Z'),
    activated_at TEXT
        CHECK (activated_at IS NULL
            OR activated_at LIKE '%+00:00' OR activated_at LIKE '%Z'),
    retired_at TEXT
        CHECK (retired_at IS NULL
            OR retired_at LIKE '%+00:00' OR retired_at LIKE '%Z'),
    validation TEXT
);

CREATE INDEX idx_dynamic_tools_state ON dynamic_tools(state);
CREATE INDEX idx_dynamic_tools_capability ON dynamic_tools(capability);
