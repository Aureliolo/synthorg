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
        limit: int = 100,
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
All `mode="before"` validators in the codebase return new dicts; the
immutability tests under `tests/unit/{api,tools}/...` lock in the
pattern.

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

Enforced at pre-push by `scripts/check_domain_error_hierarchy.py`,
which AST-walks every `class .*` definition under `src/synthorg/` and
fails the build if a class inherits directly from `Exception` /
`RuntimeError` / `LookupError` / `PermissionError` / `ValueError` /
`TypeError` / `KeyError` / `IndexError` / `AttributeError` / `OSError`
/ `IOError` without reaching `DomainError` via another base. Per-line
opt-out: `# lint-allow: domain-error-hierarchy -- <reason>`. See
[errors.md](errors.md#domain-error-hierarchy-gate) for the full gate
contract.

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
* `src/synthorg/api/ws_payloads/` for the WebSocket payload
  discriminated union (split across `_lifecycle.py` and `_domain.py`
  with the union exported from `__init__.py`).
* `src/synthorg/a2a/rpc_params.py` for the A2A JSON-RPC param
  discriminated union.

## 8. Frozen `ConfigDict` pattern

Every Pydantic model declares
`model_config = ConfigDict(frozen=True, allow_inf_nan=False)`. The
project standard is to add `extra="forbid"` on every model that does
not need to round-trip through `model_dump()` -- which is most of
them. Around 489 ConfigDicts across `src/synthorg/` carry the strict
form today; the carve-out is the ~46 classes that declare a
`@computed_field`, where Pydantic v2 includes the computed value in
`model_dump()` output and a strict-extra reconstruction would reject
that key on the round trip. Request DTOs are always strict because
the caller-side reject-unknown-keys property is what `extra="forbid"`
exists for.

Combined with the framework's `frozen` guarantee this gives us the
"create new objects, never mutate existing ones" property the
immutability covenant relies on.

References: 489+ occurrences across `src/synthorg/`. Canonical example:
`src/synthorg/approval/models.py:28`.

## 9. Typed args models at system boundaries (#1611)

Every system boundary that accepted a raw `dict[str, Any]` now
validates against a frozen Pydantic args model:

* **A2A JSON-RPC** (`src/synthorg/a2a/rpc_params.py`): one model per
  RPC method, joined into `A2ARpcParams` discriminated union via the
  `method` literal. The gateway calls `parse_rpc_params(rpc_request)`
  before dispatching.
* **WebSocket events** (`src/synthorg/api/ws_payloads/`): one model
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
  `read_tool` / `write_tool` / `admin_tool` registration *may* pass
  `args_model=<Args>` through to `MCPToolDef.args_model`; when set,
  the invoker validates ahead of dispatch and failures surface as the
  standard `ArgumentValidationError` envelope without ever calling
  the handler. `args_model` is optional (typed
  `type[BaseModel] | None` on `MCPToolDef`); registrations with
  `args_model=None` -- e.g. `MCPBridgeTool` whose shape mirrors a
  remote MCP server's `tools/list` response and is not known until
  runtime -- keep the legacy `common_args` validators inside the
  handler body. The bulk of in-tree tools declare a concrete
  `args_model`; the `None` exit is reserved for genuinely
  dynamically-shaped tools.

All args models share the convention from §8 (frozen, no NaN/Inf,
extra=forbid) and reuse the `_ArgsBase` / `PaginationFields` /
`DestructiveGuardrailFields` mixins under
`src/synthorg/meta/mcp/domains/_common_args.py` where applicable.

## 10. Pydantic v2 model conventions

Three rules apply on top of §8's frozen `ConfigDict`:

* **`NotBlankStr` for identifier / name fields.** Import from
  `synthorg.core.types` and use it for every identifier, name, or
  required-non-empty string field, including the `NotBlankStr | None`
  optional and `tuple[NotBlankStr, ...]` tuple variants. Replaces the
  manual whitespace validators that several models used to carry.
* **`@computed_field` for derived values.** Never store + validate a
  redundant field; let it derive. Canonical example:
  `TokenUsage.total_tokens` is a computed field over `prompt_tokens`
  and `completion_tokens`.
* **`allow_inf_nan=False` everywhere.** Already part of the standard
  `ConfigDict` from §8. The point is that numeric fields reject `NaN`
  and `Inf` at validation time rather than producing silent garbage
  downstream.

Reference: 30+ occurrences across `src/synthorg/`. The
`tests/unit/api/test_response_models.py` and
`tests/unit/persistence/test_token_usage.py` suites pin the
`computed_field` and `NotBlankStr` patterns.

## 11. Async concurrency: `asyncio.TaskGroup` and structured concurrency

New code uses `asyncio.TaskGroup` for fan-out / fan-in parallel work
(multiple tool invocations, parallel agent calls). Bare
`asyncio.create_task` is reserved for genuinely fire-and-forget paths
that escape the current scope (rare; prefer structured concurrency).

When running multiple tasks inside a `TaskGroup` where one task's
failure should NOT cancel the others -- independent workers,
classification detectors, notification sinks -- wrap each task body
in a small `async def` helper that catches `Exception` and returns a
safe default. Re-raise only `MemoryError` / `RecursionError` (those
indicate the interpreter itself is in trouble and the group should
unwind):

```python
async def _safe_dispatch(sink: NotificationSink, payload: Payload) -> None:
    try:
        await sink.dispatch(payload)
    except (MemoryError, RecursionError):
        raise
    except Exception:
        logger.warning(SINK_DISPATCH_FAILED, sink=sink.name, exc_info=False)

async with asyncio.TaskGroup() as tg:
    for sink in self._sinks:
        tg.create_task(_safe_dispatch(sink, payload))
```

Migration is incremental. Existing `gather(..., return_exceptions=True)`
sites are being converted as code in their vicinity changes; do not
preemptively rewrite unrelated modules.

## 12. Time injection: the `Clock` seam

Any class that reads wall-clock time, monotonic time, or sleeps
cooperatively MUST take an optional `clock: Clock | None = None`
constructor parameter that defaults to `SystemClock()` (both from
`synthorg.core.clock`):

| Replace ... | with ... |
|---|---|
| `datetime.now(UTC)` | `self._clock.now()` |
| `time.monotonic()` | `self._clock.monotonic()` |
| `await asyncio.sleep(...)` | `await self._clock.sleep(...)` |
| `time.time()` (epoch float) | `self._clock.now().timestamp()` |

The wall-clock-epoch case matters for sites that compare against an
attacker-supplied timestamp header (webhook freshness checks):
`self._clock.now().timestamp()` produces the same epoch float
without bypassing the seam.

Tests inject `FakeClock` from `tests/_shared/fake_clock.py` and drive
virtual time deterministically via `clock.advance(seconds)`,
`await clock.advance_async(seconds)`, or `await clock.sleep(seconds)`
(which advances and yields once so awaiters wake up the same way they
would under `SystemClock`).

### Sanctioned legacy callable shape

`loop_prevention/{circuit_breaker,dedup,rate_limit}.py` and
`communication/meeting/scheduler.py` deliberately stay on the older
`clock: Callable[[], float] = time.monotonic` shape. The migration
churn there (~30 test sites passing callables) outweighs the
testability win. New code uses the `Clock` Protocol; do not add new
modules to the legacy-callable list without justification.

## 13. Observability event-name inventory

Every observability event is a `Final[str]` constant in a
domain-scoped module under `src/synthorg/observability/events/`.
Import by name from the domain module; never use a string literal in
a `logger.*(...)` call.

Domains currently exposing constants (non-exhaustive; see
`src/synthorg/observability/events/__init__.py` for the live list):
`api`, `tool`, `workflow_execution`, `approval_gate`, `hr`,
`workers`, `meeting`, `engine`, `escalation`, `settings`,
`memory`, `persistence`, `mcp`, `telemetry`, `classification`,
`verification`, `rollout`, `chief_of_staff`, `analytics`,
`integrations`, `a2a`, `budget`, `coordination`, `security`,
`audit_chain`.

The `security` domain is special: every constant whose value starts
with `security.` (or `tool.registry.integrity.`) is signed and
appended to the audit chain by `AuditChainSink`. See
[docs/design/observability.md](../design/observability.md#audit-chain)
for the opt-in rule and the sink's record-shape extraction logic.

### `events/telemetry.py` namespace split

`events/telemetry.py` carries two name-spaced groups:

- `TELEMETRY_*` constants are observability log events emitted via
  `logger.*(...)`.
- `TELEMETRY_EVENT_*` constants are payload event types that go
  inside `TelemetryEvent.event_type` and ride through the privacy
  scrubber.

Pick the right namespace when adding constants. Mixing them is the
typical cause of "the scrubber rejected my new field" surprises.

### `*_STATUS_TRANSITIONED` constants

Every status enum hop (including non-terminal ones like
`PENDING -> RUNNING`) MUST log at INFO using a domain-scoped
`*_STATUS_TRANSITIONED` constant carrying `from_status`,
`to_status`, and the domain identifier. Examples:
`WORKFLOW_EXEC_STATUS_TRANSITIONED`,
`APPROVAL_STATUS_TRANSITIONED`,
`PRUNING_REQUEST_STATUS_TRANSITIONED`.

Subsystems that already have terminal-state events
(`MEETING_COMPLETED`, `WORKFLOW_EXEC_FAILED`, ...) keep those for
final-hop summaries. The transition log fires AFTER the persistence
write succeeds, so the audit trail captures only transitions that
actually landed; if pre-decision visibility is needed, emit a
separate DEBUG "attempting transition" log alongside.

## 14. Repository CRUD method names

Persistence repositories share a CRUD vocabulary that's uniform
across 100+ implementations.  This section expands on §1 with the
extra semantic detail (return-value contracts, immutability of
collection returns, where ``NotFoundError`` belongs).

| Method | Signature | Semantics |
|--------|-----------|-----------|
| `save` | `async def save(entity) -> None` | Insert or update; idempotent. One persist verb (no separate `create` / `update`). |
| `get` | `async def get(id) -> Entity \| None` | Single-entity fetch. Returns `None` on miss, never raises. |
| `delete` | `async def delete(id) -> bool` | Removal. ``True`` if a row was removed, ``False`` if the id did not exist; same return type used in §1. |
| `list_items` | `async def list_items(...) -> tuple[Entity, ...]` | Full scan / paginated list. Some older repositories use `list_all()`; new repositories prefer `list_items(*, limit, offset, **filters)` so callers can paginate without defensive slicing. |
| `query` | `async def query(...) -> tuple[Entity, ...]` | Filtered query when the filter set diverges from a single canonical `list_items`. |

Query methods always return `tuple[T, ...]`, never `list[T]`. This
matches the immutability default for collection returns and lets
callers safely share results without defensive copies.

A handful of older repositories (notably ``OntologyEntityRepository``
and ``ProjectRepository``) currently raise ``OntologyNotFoundError`` /
``RecordNotFoundError`` directly from ``get()`` instead of returning
``None``; this predates the canonical pattern and is tracked as a
follow-up migration.  New repositories follow the
``Entity | None`` shape so the service layer owns the
``NotFoundError`` raise (with the ``logger.warning(...)`` + raise
audit trail).

## 15. MCP handler logging centralisation

Every MCP handler error path uses one of three centralised helpers
from `src/synthorg/meta/mcp/handlers/common_logging.py`:

* `log_handler_argument_invalid(tool, exc)` for
  `ArgumentValidationError`
* `log_handler_invoke_failed(tool, exc, **context)` for any
  other service-layer exception
* `log_handler_guardrail_violated(tool, exc)` for
  `GuardrailViolationError`

Success paths emit `logger.info(MCP_HANDLER_INVOKE_SUCCESS,
tool_name=...)`. Do NOT emit custom `logger.error()` /
`logger.warning()` calls from handlers -- these three helpers are
the single source of truth so an event-name change touches one
file, not 200+ handler methods.

## 16. Repository file structure

* Repository protocols live in
  `src/synthorg/persistence/<domain>_protocol.py` as
  `@runtime_checkable Protocol` classes.
* Concrete implementations live in
  `src/synthorg/persistence/sqlite/<domain>_repo.py` and
  `src/synthorg/persistence/postgres/<domain>_repo.py`.
* Both backends MUST conform to the same protocol; dual-backend
  conformance is enforced via parametrised tests in
  `tests/conformance/persistence/` (the shared `backend` fixture in
  `conftest.py` runs each test against SQLite and Postgres) and
  policed by `scripts/check_dual_backend_test_parity.py` (signature
  + body + coverage passes; pre-push hook + CI Lint job).
* Every new repository MUST be exposed on `PersistenceBackend`
  (`src/synthorg/persistence/protocol.py`) as a property so
  controllers and services can resolve it through the same
  backend handle they already hold; concrete backends
  (`SQLitePersistenceBackend`, `PostgresPersistenceBackend`) fill
  in the property by constructing the per-backend repo with the
  shared connection pool.  Without this exposure, the new repo is
  unreachable through the canonical service-layer access path
  and must be hand-wired at every call site.
* The naming consistency lets `glob`-based test discovery and
  contributor onboarding find the right files without grepping.

## 17. Registering a new MANDATORY rule

Every paragraph marked `(MANDATORY)` in the canonical doc set
(`CLAUDE.md`, `web/CLAUDE.md`, `cli/CLAUDE.md`, `docs/reference/*.md`,
`docs/design/*.md`) must be registered in
`scripts/convention_gate_map.yaml` in the same PR that introduces it.
The meta-gate `scripts/check_convention_gate_inventory.py` runs at
pre-push and fails the build if a paragraph is unregistered, an entry
is stale, or a referenced gate path is missing on disk.

Each registration takes one of two shapes:

* **Gate-backed** (the default; the goal for every new rule):

  ```yaml
  - id: <file-slug>::<header-slug>
    file: <repo-relative path>
    header: <exact header text without the "(MANDATORY)" suffix>
    gate: scripts/check_<your-rule>.py
  ```

  The gate path can point at any file whose presence and correctness
  enforce the rule (a `scripts/check_*.py` AST gate, an ESLint config,
  a CI ceiling file). The path is verified to exist on disk; broken
  references fail the gate.

* **Exempt** (reserved for rules that are genuinely not script-
  enforceable -- process rules requiring user approval, workflow rules
  enforced by hookify or skills):

  ```yaml
  - id: <file-slug>::<header-slug>
    file: <repo-relative path>
    header: <exact header text>
    exempt:
      reason: |
        <one or more sentences explaining why no script can enforce
        this rule and what does (peer review, /pre-pr-review skill,
        runtime test guard, etc.)>
  ```

  Exempt entries are technical debt. The convention-rollout policy
  aims to drive the exempt list toward zero by promoting each
  exemption into a real gate as enforcement options become tractable.

### Generating the rule id

The id is `<file-slug>::<header-slug>`, both halves lowercase ASCII
slugs (alphanumeric runs separated by `-`, with the file path's `/`
treated as whitespace). Examples:

| File              | Header                      | id                                          |
| ----------------- | --------------------------- | ------------------------------------------- |
| `CLAUDE.md`       | `Persistence Boundary`      | `claude-md::persistence-boundary`           |
| `web/CLAUDE.md`   | `MSW handlers`              | `web-claude-md::msw-handlers`               |
| `docs/reference/foo.md` | `Async-Leak Ceiling`  | `docs-reference-foo-md::async-leak-ceiling` |

If you rename a header, update both `header:` and `id:` in the same
edit. The gate's stale-entry check surfaces orphans automatically.

## See also

* [persistence-boundary.md](persistence-boundary.md): repository /
  service / controller layering, plus the datetime-marshalling
  helpers (`parse_iso_utc`, `format_iso_utc`, `normalize_utc`,
  `coerce_row_timestamp`).
* [lifecycle-sync.md](lifecycle-sync.md): `_lifecycle_lock` rule.
* [pluggable-subsystems.md](pluggable-subsystems.md): protocol +
  strategy + factory + config discriminator pattern.
* [sec-prompt-safety.md](sec-prompt-safety.md): SEC-1 untrusted-content
  fences, HTML parsing guard, secret-log redaction (the `error=str(exc)`
  ban).
* [errors.md](errors.md): RFC 9457 problem details, error-code
  ranges, HTTP exception handler registration recipe.
* [mcp-handler-contract.md](mcp-handler-contract.md): the Args models
  contract at the MCP boundary (#1611).
