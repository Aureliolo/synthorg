---
title: Custom MCP Server Development
description: Add a new MCP tool by defining its tool descriptor, implementing the handler, wiring it through a feature manifest, and guarding admin actions.
---

# Custom MCP Server Development

SynthOrg's MCP surface exposes <!--RS:mcp_tools-->241<!--/RS--> tools across
<!--RS:mcp_domains-->21<!--/RS--> domain modules under
`src/synthorg/meta/mcp/domains/`. A tool is a pair: an `MCPToolDef`
*descriptor* (cheap data: name, JSON-Schema parameters, capability, optional
typed `args_model`) and a `ToolHandler` *implementation* (an async function
that runs the logic and returns a JSON envelope string). Descriptors live in
`domains/<domain>.py`; handlers live in `handlers/<domain>.py`; a feature
manifest pairs them so the composition root discovers both without importing
the service-heavy handler graph at boot.

This guide adds a `synthorg_hello_greet` read tool end to end.

## Anatomy of a tool

1. A descriptor built by `read_tool` / `write_tool` / `admin_tool`
   (`synthorg.meta.mcp.tool_builder`). Each builder names the tool
   `synthorg_<domain>_<action>`, sets the `<domain>:<read|write|admin>`
   capability, and derives a matching `handler_key`.
2. A handler coroutine with the `ToolHandler` signature
   (`synthorg.meta.mcp.handler_protocol`): keyword-only `app_state`,
   `arguments: dict[str, object]`, and `actor: AgentIdentity | None = None`,
   returning a JSON-serialised `str` envelope.
3. A `<DOMAIN>_HANDLERS: Mapping[str, ToolHandler]` map (wrapped in
   `MappingProxyType`) keyed by tool name, plus a feature manifest that
   declares the descriptor tuple and a deferred handler loader.

When a descriptor carries an `args_model`, the invoker validates the raw
arguments against that Pydantic model *before* calling the handler; failed
validation surfaces as an `invalid_argument` envelope without invoking the
handler. The handler still receives a `dict[str, object]` (the validated
model's `model_dump()`), so it can re-build the typed model locally for
strict field access. See
[typed-boundaries.md](../reference/typed-boundaries.md) for the contract.

## Worked example: a `hello.greet` tool

### 1. Declare the descriptor

Add a domain module `src/synthorg/meta/mcp/domains/hello.py`:

```python
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.tool_builder import read_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef


class GreetArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotBlankStr = Field(description="Subject of the greeting")
    times: int = Field(default=1, ge=1, le=5, description="Repeat count")


HELLO_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "hello",
        "greet",
        "Return a greeting; primarily for smoke tests.",
        {
            "name": {"type": "string", "description": "Subject of the greeting"},
            "times": {
                "type": "integer",
                "description": "Repeat count",
                "default": 1,
                "minimum": 1,
                "maximum": 5,
            },
        },
        required=("name",),
        args_model=GreetArgs,
    ),
)
```

### 2. Implement the handler

Add `src/synthorg/meta/mcp/handlers/hello.py`. Handlers never raise across the
boundary: they return `ok(...)` / `err(...)` envelopes from
`synthorg.meta.mcp.handlers.common`, and degrade to `capability_gap(...)` when
a required service is not wired.

```python
from collections.abc import Mapping
from types import MappingProxyType

from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.domains.hello import GreetArgs
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import ok
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

logger = get_logger(__name__)


async def _hello_greet(
    *,
    app_state,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_hello_greet`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_hello_greet"
    # args_model already validated at the invoker boundary; re-build for
    # typed field access (a no-op re-validation of the dumped dict).
    args = GreetArgs.model_validate(arguments)
    greeting = ", ".join([f"Hello {args.name}"] * args.times)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data={"greeting": greeting})


HELLO_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_hello_greet": _hello_greet,
    },
)
```

### 3. Wire it through a feature manifest

Declare the descriptor tuple and a deferred handler loader on the owning
feature's `feature.py` (`mcp_descriptor` defers the handler import so feature
discovery stays light):

```python
from collections.abc import Mapping

from synthorg.meta.mcp.domains.hello import HELLO_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor


def _hello_mcp_handlers() -> Mapping[str, object]:
    from synthorg.meta.mcp.handlers.hello import HELLO_HANDLERS  # noqa: PLC0415

    return HELLO_HANDLERS


# inside the feature's FeatureManifest(...):
#     mcp_handlers=(
#         mcp_descriptor(
#             domain="hello",
#             tool_defs=HELLO_TOOLS,
#             handlers=_hello_mcp_handlers,
#         ),
#     ),
```

The composition root reads `mcp_handlers` off every discovered feature to
build the registry and the dispatch handler map; there is no hand-maintained
central list. See `src/synthorg/coordination/feature.py` for a complete
manifest.

### 4. Invoke it

`MCPToolInvoker.invoke` returns a `ToolExecutionResult` whose `content` is the
JSON envelope string and whose `is_error` flag reflects the outcome:

```python
result = await invoker.invoke(
    "synthorg_hello_greet",
    {"name": "world", "times": 2},
    app_state=app_state,
    actor=actor,
)
assert not result.is_error
# result.content == '{"status": "ok", "data": {"greeting": "Hello world, Hello world"}}'
```

## Admin guardrails

Tools that mutate global state (delete agents, rotate secrets) MUST use
`admin_tool` and call `require_admin_guardrails(...)` as the lexically first
statement in the handler. The descriptor spreads `**ADMIN_GUARDRAIL_PROPERTIES`
into its parameters and lists `("reason", "confirm")` as required, so the wire
contract matches the handler-side check:

```python
from synthorg.meta.mcp.handlers.common import require_admin_guardrails


async def _agent_delete(
    *,
    app_state,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    reason, actor = require_admin_guardrails(arguments, actor)
    # ... mutation logic, attributed to ``actor`` with ``reason`` ...
```

`require_admin_guardrails` enforces three preconditions in order: a non-`None`
`actor` with an audit-usable identifier; `arguments["confirm"]` is the literal
`True`; `arguments["reason"]` is a non-blank string. Any failure raises
`GuardrailViolationError`, which the invoker converts to a
`guardrail_violated` error envelope. A small set of admin tools opt out of the
`reason`/`confirm` parameters with a justified
`# lint-allow: mcp-admin-guardrail -- <reason>` annotation enforced by
`scripts/check_mcp_admin_tool_guardrails.py`. See
[mcp-handler-contract.md](../reference/mcp-handler-contract.md) for the full
contract.

## Observability

Every dispatch emits structured events and metrics:

- `MCP_SERVER_INVOKE_START` at boundary entry; `MCP_HANDLER_INVOKE_SUCCESS` on
  a returned `ok` envelope; `MCP_SERVER_INVOKE_FAILED` on tool/handler lookup
  failure, validation failure, or guardrail rejection.
- The `synthorg_mcp_handler_outcomes_total` counter and
  `synthorg_mcp_handler_duration_seconds` histogram both carry `tool` and
  `outcome` labels with bounded values from `VALID_MCP_HANDLER_OUTCOMES`.

## Testing

Add `tests/unit/mcp/domains/test_hello.py`. Drive the real invoker and assert
on the parsed envelope:

```python
import json

import pytest


@pytest.mark.unit
async def test_greet_returns_repeated_greeting(mcp_invoker, app_state, actor) -> None:
    result = await mcp_invoker.invoke(
        "synthorg_hello_greet",
        {"name": "tester", "times": 3},
        app_state=app_state,
        actor=actor,
    )
    assert not result.is_error
    body = json.loads(result.content)
    assert body["data"]["greeting"].count("Hello tester") == 3


@pytest.mark.unit
async def test_greet_rejects_extra_keys(mcp_invoker, app_state, actor) -> None:
    result = await mcp_invoker.invoke(
        "synthorg_hello_greet",
        {"name": "x", "color": "blue"},
        app_state=app_state,
        actor=actor,
    )
    assert result.is_error
```

See the existing domain tests under `tests/unit/mcp/` for the shared fixtures.
