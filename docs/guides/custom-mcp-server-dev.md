---
title: Custom MCP Server Development
description: Register a new MCP tool, define its typed args model, wire admin guardrails.
---

# Custom MCP Server Development

SynthOrg's MCP surface exposes 200+ tools across 15 domain modules under `src/synthorg/meta/mcp/domains/`. Each tool is a `ToolHandler` with an optional `args_model` that drives the typed boundary. This guide shows how to register a hello-world tool, validate its arguments, and surface it to operators.

## Anatomy of a tool

A tool consists of:

1. A `MCPToolDef` with `name`, `description`, optional `args_model`.
2. A handler coroutine `(app_state, arguments, **kwargs) -> dict`.
3. Registration in the relevant domain module.

The invoker (`src/synthorg/meta/mcp/invoker.py`) routes args validation through `parse_typed("mcp.tool", ...)` when `args_model` is set; otherwise the handler's `common_args` helpers do field-level validation. Both paths converge on the `ArgumentValidationError` envelope with `domain_code=invalid_argument` on failure. See [docs/reference/typed-boundaries.md](../reference/typed-boundaries.md) for the dual-path contract.

## Worked example: a `hello.greet` tool

Define the args model under a new domain file:

```python
# src/synthorg/meta/mcp/domains/hello/handler.py
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.registry import MCPToolDef, register_tool


class GreetArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotBlankStr = Field(description="Subject of the greeting")
    times: int = Field(default=1, ge=1, le=5, description="Repeat count")


async def greet(
    *,
    app_state,
    arguments: dict,
    actor_id: str,
    **_: object,
) -> dict:
    args = GreetArgs.model_validate(arguments)
    greeting = ", ".join([f"Hello {args.name}"] * args.times)
    return {"status": "ok", "greeting": greeting}


register_tool(
    MCPToolDef(
        name="hello.greet",
        description="Return a greeting; primarily for smoke tests.",
        args_model=GreetArgs,
        handler=greet,
        operation_type="read",
    )
)
```

Register the domain module in the MCP boot path (see existing domains under `src/synthorg/meta/mcp/domains/` for the pattern).

Invoke the tool through the MCP client:

```python
from synthorg.meta.mcp.invoker import invoke

result = await invoke(
    app_state=app_state,
    tool_name="hello.greet",
    arguments={"name": "world", "times": 2},
    actor_id="agent-007",
)
print(result.content)  # {"status": "ok", "greeting": "Hello world, Hello world"}
```

## Admin guardrails

Tools that mutate global state (delete agents, rotate secrets, etc.) MUST guard against unprivileged callers. Call `require_admin_guardrails(...)` at the start of the handler:

```python
from synthorg.security.guardrails import require_admin_guardrails

async def delete_agent(*, app_state, arguments, actor_id, **_):
    await require_admin_guardrails(
        actor_id=actor_id,
        action="agent.delete",
        app_state=app_state,
    )
    # ... mutation logic ...
```

The guardrail emits `mcp.admin.denied` on rejection (with `actor_id`, `action`, `reason`) and the invoker returns the `forbidden` envelope. See [docs/reference/mcp-handler-contract.md](../reference/mcp-handler-contract.md) for the full contract.

## Observability

Every dispatch emits:

- `mcp.server.invoke.start`: at boundary entry (after auth).
- `mcp.server.invoke.success`: on a returned dict.
- `mcp.server.invoke.failed`: on validation, exception, or guardrail rejection.

The `synthorg_mcp_handler_outcomes_total` counter and `synthorg_mcp_handler_duration_seconds` histogram both carry `tool` and `outcome` labels with bounded values from `VALID_MCP_HANDLER_OUTCOMES`.

## Testing

Add `tests/unit/mcp/domains/test_hello.py`:

```python
import pytest

from synthorg.meta.mcp.invoker import invoke


@pytest.mark.unit
async def test_greet_returns_repeated_greeting(app_state) -> None:
    result = await invoke(
        app_state=app_state,
        tool_name="hello.greet",
        arguments={"name": "tester", "times": 3},
        actor_id="agent-test",
    )
    assert not result.is_error
    body = result.json_body()
    assert body["greeting"].count("Hello tester") == 3


@pytest.mark.unit
async def test_greet_rejects_extra_keys(app_state) -> None:
    result = await invoke(
        app_state=app_state,
        tool_name="hello.greet",
        arguments={"name": "x", "color": "blue"},
        actor_id="agent-test",
    )
    assert result.is_error
    assert "extra" in result.error_message.lower()
```

The `app_state` fixture is shared across MCP tests; see `tests/unit/mcp/conftest.py`.
