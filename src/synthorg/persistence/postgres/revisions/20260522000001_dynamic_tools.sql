-- depends: 20260521000001_external_api_governed_access

-- Self-extending toolkit: authored tool blueprints.
--
-- dynamic_tools stores runtime-authored MCP tools. A blueprint is a
-- declarative spec (name, capability, JSON Schema) plus a sandbox
-- script body, governed through the TOOL_CREATION proposal altitude.
-- state drives the lifecycle: pending (proposed) -> validated (passed
-- the benchmark gate) -> active (live-registered) -> retired (rolled
-- back). The state-correlated timestamps are stamped on transition.
-- parameters_schema and validation use native JSONB; a real Pydantic
-- args_model is materialised from parameters_schema at registration.
-- JSON columns reject non-object payloads so a malformed blueprint
-- cannot persist; the table-level CHECK enforces the lifecycle so
-- timestamps must match the state.

CREATE TABLE dynamic_tools (
    id TEXT NOT NULL PRIMARY KEY CHECK (char_length(trim(id)) > 0),
    name TEXT NOT NULL UNIQUE CHECK (char_length(trim(name)) > 0),
    description TEXT NOT NULL CHECK (char_length(trim(description)) > 0),
    capability TEXT NOT NULL CHECK (char_length(trim(capability)) > 0),
    parameters_schema JSONB NOT NULL
        CHECK (jsonb_typeof(parameters_schema) = 'object'),
    script_body TEXT NOT NULL CHECK (char_length(trim(script_body)) > 0),
    sandbox_backend TEXT NOT NULL
        CHECK (sandbox_backend IN ('docker', 'subprocess')),
    requires_network BOOLEAN NOT NULL DEFAULT FALSE,
    action_type TEXT NOT NULL CHECK (char_length(trim(action_type)) > 0),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'validated', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL,
    validated_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    validation JSONB
        CHECK (validation IS NULL OR jsonb_typeof(validation) = 'object'),
    CHECK (
        (state = 'pending'
            AND validated_at IS NULL
            AND activated_at IS NULL
            AND retired_at IS NULL)
        OR (state = 'validated'
            AND validated_at IS NOT NULL
            AND activated_at IS NULL
            AND retired_at IS NULL
            AND validation IS NOT NULL)
        OR (state = 'active'
            AND validated_at IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NULL
            AND validation IS NOT NULL)
        OR (state = 'retired'
            AND validated_at IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NOT NULL
            AND validation IS NOT NULL)
    )
);

CREATE INDEX idx_dynamic_tools_state ON dynamic_tools(state);
CREATE INDEX idx_dynamic_tools_capability ON dynamic_tools(capability);
