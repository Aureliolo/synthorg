---
title: Implicit Conventions
description: Patterns followed across the SynthOrg codebase that are not codified in CLAUDE.md but are universally observed.
---

# Implicit Conventions

`CLAUDE.md` codifies the rules we enforce with hooks and review agents.
This page captures the *implicit* conventions: patterns followed
across the codebase by precedent rather than enforcement. New code
should follow them; deviations should be justified in the diff.

## 1. Repository CRUD signature pattern

Every repository protocol exposes the same five-method shape, returning
`bool` from `delete` so callers can distinguish "removed one row" from
"id did not exist" without raising.

```python
class ApprovalRepository(Protocol):
    async def save(self, item: ApprovalItem) -> None: ...
    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None: ...
    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]: ...
    async def delete(self, approval_id: NotBlankStr) -> bool: ...
```

Reference: `src/synthorg/persistence/approval_protocol.py:34-73`.

## 2. Service lifecycle method symmetry

Long-lived services own a private `asyncio.Lock` named
`_lifecycle_lock` (separate from any hot-path lock) and expose
symmetric `async start()` / `async stop()` methods. Both must be
held across the full body of their respective method per
[lifecycle-sync.md](lifecycle-sync.md). Examples:
`src/synthorg/workers/worker.py`,
`src/synthorg/integrations/health/prober.py`.

## 3. API response wrapping

Controllers return one of three shapes:

* `ApiResponse[T]`: success-only path with no header or status-code
  customization. Litestar serializes it as `{"data": ..., "error": null,
  "success": true}`.
* `PaginatedResponse[T]`: list endpoints that return a page of items
  plus pagination metadata. Wraps `ApiResponse[T]` and adds a
  `pagination` envelope ({`limit`, `offset`, `total`, `next_cursor`,
  `has_more`}). Required for any controller method whose return type
  is a collection. Opaque cursor pagination is the project default.
* `Response[ApiResponse[T]]`: only when status code or response
  headers must be customized (e.g. setting a `Location` header on a
  201, attaching a `Retry-After` header on a 429).

Reference: `src/synthorg/api/dto.py` (`ApiResponse`,
`PaginatedResponse`, `PaginationMeta`); almost every controller under
`src/synthorg/api/controllers/`.

## 4. `@model_validator(mode="after")` is the default

`mode="after"` runs against the constructed model and is the default
choice. `mode="before"` is reserved for normalizing inputs the caller
might pass in non-canonical shape (lists vs tuples, dirty strings,
missing aliases). When using `mode="before"`, **never** mutate the
input dict in place; return a new dict via `{**data, key: value}`.
The four sites flagged by the 2026-04-25 audit have been converted;
the new immutability tests in `tests/unit/{api,tools}/...` lock
in the pattern.

## 5. Event constant module imports

Every observability event is defined as a `Final[str]` constant under
`src/synthorg/observability/events/<domain>.py` and imported by name
(never by string literal) from the consumer.

```python
from synthorg.observability.events.workers import (
    WORKERS_DISPATCHER_CLAIM_ENQUEUED,
    WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
    WORKERS_DISPATCHER_PUBLISH_FAILED,
    WORKERS_DISPATCHER_PUBLISH_RETRYING,
    WORKERS_DISPATCHER_QUEUE_NOT_RUNNING,
)
```

Reference: `src/synthorg/workers/dispatcher.py:19-25`.

## 6. Domain error hierarchies

Each domain owns `errors.py` with a base error class carrying
`status_code` / `error_code` / `error_category` / `retryable` as
`ClassVar`s; subclasses inherit and override only the fields that
change. The HTTP exception handler keys off the base class so a new
subclass automatically inherits the correct status mapping.

References:

* `src/synthorg/budget/errors.py`: `BudgetExhaustedError` family.
* `src/synthorg/communication/errors.py`: `CommunicationError`
  family.
* `src/synthorg/engine/errors.py`: `EngineError` family.

## 7. Module file structure

Every business-logic module follows the same top-down ordering:

1. Module docstring.
2. Imports: stdlib, then third-party, then internal, alphabetical
   within each group.
3. `logger = get_logger(__name__)` immediately after imports.
4. Module-level `Final` constants (private prefixed with `_`).
5. Public types (Pydantic models, dataclasses, enums).
6. Public functions / classes.
7. Private helpers (prefixed with `_`).

Reference: `src/synthorg/communication/bus/memory.py`.

For tool / handler argument models (Pydantic), the convention is to
co-locate them in a single domain-scoped `_args.py` module rather than
inline with the consumer. Examples:

* `src/synthorg/tools/<domain>/_args.py` for `BaseTool` subclasses
  (`file_system/_args.py`, `web/_args.py`, `database/_args.py`,
  `communication/_args.py`, `analytics/_args.py`, `design/_args.py`);
  smaller domains share an aggregator module
  (`tools/_git_args.py`, `tools/_misc_args.py`).
* `src/synthorg/meta/mcp/domains/_*_args.py` for MCP tool
  registrations (`_common_args.py`, `_tasks_args.py`,
  `_agents_args.py`, `_simple_args.py`, `_workflows_org_args.py`,
  `_remaining_args.py`).
* `src/synthorg/memory/self_editing_args.py` for the six
  self-editing-memory tools.
* `src/synthorg/api/ws_payloads.py` for the WebSocket payload
  discriminated union.
* `src/synthorg/a2a/rpc_params.py` for the A2A JSON-RPC param
  discriminated union.

## 8. Frozen `ConfigDict` pattern

Every Pydantic model declares
`model_config = ConfigDict(frozen=True, allow_inf_nan=False)`. Request
DTOs additionally set `extra="forbid"` so unknown keys are rejected
instead of silently ignored. Combined with the framework's `frozen`
guarantee this gives us the "create new objects, never mutate
existing ones" property the immutability covenant relies on.

References: 30+ occurrences across `src/synthorg/`. Canonical example:
`src/synthorg/approval/models.py:28`.

## 9. Typed args models at system boundaries (#1611)

Every system boundary that accepted a raw `dict[str, Any]` now
validates against a frozen Pydantic args model:

* **A2A JSON-RPC** (`src/synthorg/a2a/rpc_params.py`): one model per
  RPC method, joined into `A2ARpcParams` discriminated union via the
  `method` literal. The gateway calls `parse_rpc_params(rpc_request)`
  before dispatching.
* **WebSocket events** (`src/synthorg/api/ws_payloads.py`): one model
  per `WsEventType` value, joined into `WsEventPayload` discriminated
  union via `event_type`. `WsEvent` runs every constructed payload
  through the union adapter so shape drift is rejected at construction.
* **Self-editing memory** (`src/synthorg/memory/self_editing_args.py`):
  six args models discriminated by the `tool` literal.
  `parse_self_editing_args(tool_name, arguments)` validates before
  dispatch; the dispatcher matches on the typed variant.
* **Tool ecosystem** (`src/synthorg/tools/`): every concrete
  `BaseTool` subclass declares
  `args_model: ClassVar[type[BaseModel] | None] = <Args>`. The
  `ToolInvoker` calls `args_model.model_validate(arguments)` before
  invoking `execute`; failures surface as a typed
  `ToolParameterError` envelope.
* **MCP handlers** (`src/synthorg/meta/mcp/`): every
  `read_tool` / `write_tool` / `admin_tool` registration passes
  `args_model=<Args>` through to `MCPToolDef.args_model`. The
  invoker validates ahead of dispatch; failures surface as the
  standard `ArgumentValidationError` envelope without ever calling
  the handler. Handlers retain the legacy `common_args` validators
  only for tools with `args_model=None` (e.g. `MCPBridgeTool` whose
  shape is dynamic).

All args models share the convention from §8 (frozen, no NaN/Inf,
extra=forbid) and reuse the `_ArgsBase` / `PaginationFields` /
`DestructiveGuardrailFields` mixins under
`src/synthorg/meta/mcp/domains/_common_args.py` where applicable.

## See also

* [persistence-boundary.md](persistence-boundary.md): repository /
  service / controller layering.
* [lifecycle-sync.md](lifecycle-sync.md): `_lifecycle_lock` rule.
* [pluggable-subsystems.md](pluggable-subsystems.md): protocol +
  strategy + factory + config discriminator pattern.
