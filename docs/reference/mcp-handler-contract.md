# MCP Handler Contract

On-demand reference for implementing tool handlers in `src/synthorg/meta/mcp/handlers/<domain>.py`. The overview / invariants live in `CLAUDE.md` under "MCP Handler Layer". See also `docs/design/tools.md` §"SynthOrg MCP Tool Surface" for the user-facing contract and `docs/design/observability.md` §"MCP handler events" for the event inventory.

## Surface

SynthOrg exposes 200+ tools across the 15 domain modules under `src/synthorg/meta/mcp/domains/` (tasks, agents, meta, budget, analytics, coordination, quality, signals, approvals, workflows, organization, communication, integrations, infrastructure, memory). Tools are classified by capability action (`read_tool` / `write_tool` / `admin_tool`) via the builders in `src/synthorg/meta/mcp/tool_builder.py`; only the `admin_tool` subset is destructive and subject to the guardrail triple.

## ToolHandler protocol

**Signature**: `async def _<tool>(*, app_state, arguments: dict[str, Any], actor: AgentIdentity | None = None) -> str`. The `actor` kwarg threads calling-agent identity through the invoker so destructive-op guardrails can attribute audit records. Handlers that don't care about identity still accept and ignore it.

## Argument validation (typed-args path, #1611 Phase 4)

Each builder accepts an optional `args_model: type[BaseModel]` kwarg that flows through to `MCPToolDef.args_model`. When set, the invoker validates the raw `arguments` dict against the Pydantic model **before** dispatching to the handler. The validation call routes through the canonical typed-boundary helper (`synthorg.api.boundary.parse_typed("mcp.tool", arguments, args_model)`) so a malformed payload emits the cross-boundary `api.boundary.validation_failed` warning alongside the existing `mcp.server.invoke.failed` event -- see [typed-boundaries.md](typed-boundaries.md) for the full contract.

- Validation success: the invoker takes the validated model's `model_dump(mode="python")` and passes that dict to the handler. Every key matches the model's declared fields with no extras (because args models use `extra="forbid"`); enum/datetime/etc. values are already coerced.
- Validation failure: the invoker short-circuits with a `{"status": "error", "error_type": "ArgumentValidationError", "domain_code": "invalid_argument", "message": "...", "tool": ...}` envelope. The handler is never invoked.

The handler's signature stays at `arguments: dict[str, Any]` (the protocol contract) so existing handlers don't need to migrate; the dict is just structurally sound after validation. Handlers that want compile-time typed access can call `args_model.model_validate(arguments)` locally (a no-op re-validate that returns the typed model with full mypy-strict field access):

```python
async def list_tasks_handler(*, app_state, arguments: dict[str, Any], actor=None) -> str:
    args = TasksListArgs.model_validate(arguments)  # typed access from here on
    page = await app_state.task_service.list(
        status=args.status, offset=args.offset, limit=args.limit,
    )
    return ok(data=...)
```

Tools without an `args_model` (legacy / dynamically-shaped tools such as `MCPBridgeTool`) keep the manual `common_args` validation path described below.

## Argument validation (legacy `common_args` path)

## Shared helper modules

Three sibling modules under `src/synthorg/meta/mcp/handlers/` carry the handler infrastructure; pick the right module when importing helpers:

- **`common.py`**: response envelopes (`ok`, `err`, `not_supported`, `capability_gap`, `service_fallback`), pagination output (`PaginationMeta`, `paginate_sequence`, `dump_many`), guardrails (`require_admin_guardrails`), placeholder factories (`make_placeholder_handler`, `make_handlers_for_tools`).
- **`common_args.py`**: every argument validator/extractor: `require_arg`, `require_non_blank`, `coerce_pagination`, `actor_id`, `require_actor_id`, `actor_label`, `get_optional_str`, `require_dict`, `parse_time_window`, `parse_str_sequence`.
- **`common_logging.py`**: structured-logging helpers for the three handler-layer log paths: `log_handler_argument_invalid`, `log_handler_invoke_failed` (accepts `**context` kwargs for correlation ids), `log_handler_guardrail_violated`. Owns a module-scoped logger keyed at `synthorg.meta.mcp.handlers` so test assertions see a single stable event source regardless of which domain emitted the event.

## Envelope

Return a JSON string built by helpers in `common.py`:

- `ok(data, pagination=...)` for success.
- `err(exc)` for caught errors. Envelope picks up `domain_code="invalid_argument"` automatically on `ArgumentValidationError` / `GuardrailViolationError`. Set custom codes via `err(exc, domain_code="...")`.
- `capability_gap(tool, reason)` when the handler is wired but the underlying primitive does not expose the required method. Emits `MCP_HANDLER_CAPABILITY_GAP` at INFO.
- `not_supported(tool, reason)` for tools registered without a concrete handler. Emits `MCP_HANDLER_NOT_IMPLEMENTED` at WARNING.

Never emit a bare `{"status": "not_implemented"}` payload; `make_placeholder_handler` delegates to `not_supported()` so every unwired tool ships the single agreed envelope. The `service_fallback()` helper is retained in `common.py` but has zero call sites after META-MCP-2; `tests/integration/mcp/test_tool_surface.py` asserts zero `MCP_HANDLER_SERVICE_FALLBACK` emissions across the full 204-tool surface.

Use the helpers in `common_args.py` for tools without `args_model`:

- `require_arg(arguments, key, ty)` for typed required extraction.
- `require_non_blank(arguments, key)` for required non-blank strings.
- `get_optional_str(arguments, key)` for optional non-blank strings (returns `None` when missing).
- `require_dict(arguments, key, *, value_type=None, deep_copy=True)` for dict args; pass `value_type=str` for `dict[str, str]` validation.
- `parse_time_window(arguments, *, until_required=True)` for ISO 8601 since/until parsing.
- `parse_str_sequence(arguments, key)` for optional sequence-of-non-blank-strings args.
- `coerce_pagination(arguments, *, default_limit=50)` for offset/limit with bool rejection and bound enforcement. (MCP tools default to 50; this is intentionally lower than the repository-layer `DEFAULT_LIST_LIMIT = 100` so paginated MCP responses stay terse for assistants.)

For actor identity: use `actor_id(actor)` for optional attribution, `require_actor_id(actor)` when attribution is mandatory (raises if missing), and `actor_label(actor)` only for emit-only paths where a `"mcp-anonymous"` fallback is acceptable.

In every case, catch `ArgumentValidationError` and return `err(exc)`. Never let raw `TypeError` / `ValueError` escape from `int(...)` / enum coercion; wrap them and call `invalid_argument(name, expected)`.

## Structured logging

Three centralized helpers in `common_logging.py`; handlers must not redeclare them locally:

- `log_handler_argument_invalid(tool, exc)` after catching `ArgumentValidationError`.
- `log_handler_invoke_failed(tool, exc, **context)` after catching a generic `Exception`. Pass correlation ids (e.g. `task_id=`, `decision_id=`) as keyword args. Keys that would shadow the canonical event fields (`tool_name`, `error_type`, `error`, `event`, `log_level`) are rejected with `ValueError` so audit trails cannot be silently corrupted.
- `log_handler_guardrail_violated(tool, exc)` after catching `GuardrailViolationError`.

All three route exception messages through `safe_error_description` (SEC-1) so secret-shaped fragments are scrubbed before reaching logs. Context kwargs on `log_handler_invoke_failed` are forwarded verbatim and are NOT scrubbed; callers must not pass secrets through `**context`.

## Admin Tool Guardrails

Every handler whose tool is registered through
`synthorg.meta.mcp.tool_builder.admin_tool` calls
`require_admin_guardrails(arguments, actor)` as the **lexically first
call** in its body. It enforces:

- non-`None` `actor` carrying an audit-usable identifier (`.id` or non-blank `.name`)
- literal `confirm=True` (truthy non-bools are rejected)
- non-blank `reason`

and raises `GuardrailViolationError` with a typed `violation: Literal["missing_actor", "missing_confirm", "missing_reason"]`. Emit `MCP_ADMIN_OP_EXECUTED` exactly once per successful admin call for the audit trail. Schema-level reject whitespace reasons with `"minLength": 1, "pattern": r".*\S.*"`.

Enforced by `scripts/check_mcp_admin_tool_guardrails.py` (pre-push gate). Per-line opt-out in the function header span: `# lint-allow: mcp-admin-guardrail -- <reason>` (mandatory non-empty justification). Handlers whose admin-guardrail broadening is deferred to a follow-up issue carry the marker pointing at that follow-up.

## Registries

Export `XXX_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType({...})` so the registry is read-only. `build_handler_map()` aggregates across domains and raises on duplicate keys.

## Domain codes

Standard wire codes: `invalid_argument`, `guardrail_violated`, `not_supported`, `not_found`, `conflict`.

## Persistence boundary still applies

Handlers route through service-layer facades (`MemoryService`, `ArtifactService`, etc.), never into `app_state.persistence.*` directly. Reads that need the total count alongside a page should make the service return `(items, total)`.
