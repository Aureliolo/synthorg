# MCP Handler Contract

On-demand reference for implementing tool handlers in `src/synthorg/meta/mcp/handlers/<domain>.py`. The overview / invariants live in `CLAUDE.md` under "MCP Handler Layer". See also `docs/design/tools.md` §"SynthOrg MCP Tool Surface" for the user-facing contract and `docs/design/observability.md` §"MCP handler events" for the event inventory.

## Surface

SynthOrg exposes 200+ tools across 15 domains via its MCP server. Tools are classified by capability action (`read_tool` / `write_tool` / `admin_tool`) via the builders in `src/synthorg/meta/mcp/tool_builder.py`; only the `admin_tool` subset is destructive and subject to the guardrail triple.

## ToolHandler protocol

**Signature**: `async def _<tool>(*, app_state, arguments, actor: AgentIdentity | None = None) -> str`. The `actor` kwarg threads calling-agent identity through the invoker so destructive-op guardrails can attribute audit records. Handlers that don't care about identity still accept and ignore it.

## Shared helper modules

Three sibling modules under `src/synthorg/meta/mcp/handlers/` carry the handler infrastructure -- pick the right module when importing helpers:

- **`common.py`** -- response envelopes (`ok`, `err`, `not_supported`, `capability_gap`, `service_fallback`), pagination output (`PaginationMeta`, `paginate_sequence`, `dump_many`), guardrails (`require_destructive_guardrails`), placeholder factories (`make_placeholder_handler`, `make_handlers_for_tools`).
- **`common_args.py`** -- every argument validator/extractor: `require_arg`, `require_non_blank`, `coerce_pagination`, `actor_id`, `require_actor_id`, `actor_label`, `get_optional_str`, `require_dict`, `parse_time_window`, `parse_str_sequence`.
- **`common_logging.py`** -- structured-logging helpers for the three handler-layer log paths: `log_handler_argument_invalid`, `log_handler_invoke_failed` (accepts `**context` kwargs for correlation ids), `log_handler_guardrail_violated`. Owns a module-scoped logger keyed at `synthorg.meta.mcp.handlers` so test assertions see a single stable event source regardless of which domain emitted the event.

## Envelope

Return a JSON string built by helpers in `common.py`:

- `ok(data, pagination=...)` for success.
- `err(exc)` for caught errors. Envelope picks up `domain_code="invalid_argument"` automatically on `ArgumentValidationError` / `GuardrailViolationError`. Set custom codes via `err(exc, domain_code="...")`.
- `capability_gap(tool, reason)` when the handler is wired but the underlying primitive does not expose the required method. Emits `MCP_HANDLER_CAPABILITY_GAP` at INFO.
- `not_supported(tool, reason)` for tools registered without a concrete handler. Emits `MCP_HANDLER_NOT_IMPLEMENTED` at WARNING.

Never emit a bare `{"status": "not_implemented"}` payload; `make_placeholder_handler` delegates to `not_supported()` so every unwired tool ships the single agreed envelope. The `service_fallback()` helper is retained in `common.py` but has zero call sites after META-MCP-2; `tests/integration/mcp/test_tool_surface.py` asserts zero `MCP_HANDLER_SERVICE_FALLBACK` emissions across the full 204-tool surface.

## Argument validation

Use the helpers in `common_args.py`:

- `require_arg(arguments, key, ty)` for typed required extraction.
- `require_non_blank(arguments, key)` for required non-blank strings.
- `get_optional_str(arguments, key)` for optional non-blank strings (returns `None` when missing).
- `require_dict(arguments, key, *, value_type=None, deep_copy=True)` for dict args; pass `value_type=str` for `dict[str, str]` validation.
- `parse_time_window(arguments, *, until_required=True)` for ISO 8601 since/until parsing.
- `parse_str_sequence(arguments, key)` for optional sequence-of-non-blank-strings args.
- `coerce_pagination(arguments, *, default_limit=50)` for offset/limit with bool rejection and bound enforcement.

For actor identity: use `actor_id(actor)` for optional attribution, `require_actor_id(actor)` when attribution is mandatory (raises if missing), and `actor_label(actor)` only for emit-only paths where a `"mcp-anonymous"` fallback is acceptable.

In every case, catch `ArgumentValidationError` and return `err(exc)`. Never let raw `TypeError` / `ValueError` escape from `int(...)` / enum coercion; wrap them and call `invalid_argument(name, expected)`.

## Structured logging

Three centralized helpers in `common_logging.py` -- handlers must not redeclare them locally:

- `log_handler_argument_invalid(tool, exc)` after catching `ArgumentValidationError`.
- `log_handler_invoke_failed(tool, exc, **context)` after catching a generic `Exception`. Pass correlation ids (e.g. `task_id=`, `decision_id=`) as keyword args. Keys that would shadow the canonical event fields (`tool_name`, `error_type`, `error`, `event`, `log_level`) are rejected with `ValueError` so audit trails cannot be silently corrupted.
- `log_handler_guardrail_violated(tool, exc)` after catching `GuardrailViolationError`.

All three route exception messages through `safe_error_description` (SEC-1) so secret-shaped fragments are scrubbed before reaching logs. Context kwargs on `log_handler_invoke_failed` are forwarded verbatim and are NOT scrubbed -- callers must not pass secrets through `**context`.

## Destructive ops

Call `require_destructive_guardrails(arguments, actor)` first. It enforces:

- non-`None` `actor`
- literal `confirm=True`
- non-blank `reason`

and raises `GuardrailViolationError` with a typed `violation: Literal["missing_actor", "missing_confirm", "missing_reason"]`. Emit `MCP_DESTRUCTIVE_OP_EXECUTED` exactly once per successful destructive call for the audit trail. Schema-level reject whitespace reasons with `"minLength": 1, "pattern": r".*\S.*"`.

## Registries

Export `XXX_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType({...})` so the registry is read-only. `build_handler_map()` aggregates across domains and raises on duplicate keys.

## Domain codes

Standard wire codes: `invalid_argument`, `guardrail_violated`, `not_supported`, `not_found`, `conflict`.

## Persistence boundary still applies

Handlers route through service-layer facades (`MemoryService`, `ArtifactService`, etc.), never into `app_state.persistence.*` directly. Reads that need the total count alongside a page should make the service return `(items, total)`.
