# Typed Boundaries

The security-sensitive API entry points listed below split into two
groups. The **parse_typed-enforced** boundaries (`jwt`,
`settings.security`, `ws.control`, `audit_chain`, `a2a.jsonrpc`,
`mcp.tool`: the original six) validate inbound payloads through a
single helper, `synthorg.api.boundary.parse_typed`. The
**informational/lenient** entries (`provider.tool_call`,
`webhook.payload`, `mcp.tool.dual_path`) are documented in the same
table for discoverability but are NOT gated by `parse_typed`; the
table's `Model` column marks them explicitly (`no Pydantic`,
`extra="allow"`, dual-path helpers). The helper replaces the legacy
`dict[str, Any]` contract that let a typo or rename slip silently
through dict access at the auth, agent tool plane, audit trail, RPC,
and settings surfaces.

## The helper

```python
from synthorg.api.boundary import parse_typed

claims = parse_typed("jwt", raw_payload, JwtClaims)
user_id = claims.sub
```

Two overloads are accepted:

- `parse_typed[T: BaseModel](boundary, raw, model: type[T]) -> T` for
  single-shape boundaries (JWT, settings, audit chain).
- `parse_typed[T](boundary, raw, model: TypeAdapter[T]) -> T` for
  discriminated-union boundaries (A2A JSON-RPC params, WebSocket
  control messages).

Behaviour:

- The `boundary` label is typed `LiteralString`; passing a
  runtime-derived label fails the static type check, so the
  operator-search log key cannot be operator-influenced.
- A `None` raw payload is normalised to `{}` so callers do not branch
  on optional / nullable wire fields; Pydantic still raises loudly for
  required fields.
- On validation failure the helper logs `api.boundary.validation_failed`
  at warning with the boundary label, exception class, error count,
  redacted error description (`safe_error_description`), the first
  five field locations, and a `truncated` flag, then re-raises the
  underlying `ValidationError`. Each boundary translates the re-raised
  exception into its native error envelope or event (HTTP 422 for
  settings-import; MCP envelope `err()` with
  `domain_code=invalid_argument`; WebSocket
  `{"error": "Invalid control message"}` envelope on the open socket
  (no close-code escalation); A2A JSON-RPC `-32602 Invalid params`;
  audit-chain `audit_chain.emit_validation_failed`).

## Registered boundaries

| Boundary label       | File                                              | Function                  | Model                                                   |
| -------------------- | ------------------------------------------------- | ------------------------- | ------------------------------------------------------- |
| `jwt`                | `src/synthorg/api/auth/service.py`                | `decode_token`            | `synthorg.api.auth.claims.JwtClaims`                    |
| `settings.security`  | `src/synthorg/api/controllers/settings.py`        | `import_security_config`  | `synthorg.security.config.SecurityConfig`               |
| `ws.control`         | `src/synthorg/api/controllers/ws_protocol.py`     | `handle_message`          | `synthorg.api.ws_control_models.WsControlMessage`       |
| `audit_chain`        | `src/synthorg/observability/audit_chain/sink.py`  | `emit`                    | `synthorg.observability.audit_chain.payloads.AuditChainEventPayload` |
| `a2a.jsonrpc`        | `src/synthorg/a2a/rpc_params.py`                  | `parse_rpc_params`        | `synthorg.a2a.rpc_params.A2ARpcParams` (TypeAdapter)    |
| `mcp.tool`           | `src/synthorg/meta/mcp/invoker.py`                | `invoke`                  | Per-tool `MCPToolDef.args_model`                        |
| `provider.tool_call` | `src/synthorg/providers/drivers/mappers.py`       | `extract_tool_calls`      | (no Pydantic; lenient dict/object extraction)           |
| `webhook.payload`    | `src/synthorg/api/controllers/_webhooks_wiring.py`| `parse_payload`           | `WebhookEventPayload` (extra="allow")                   |
| `mcp.tool.dual_path` | `src/synthorg/meta/mcp/invoker.py`                | `invoke`                  | Per-tool `args_model` OR `common_args` handler helpers  |

## Per-boundary notes

### JWT (`jwt`)

`AuthService.create_token(user)` builds a `JwtClaims` instance
internally, then `model_dump(mode="json")` for the JWT library.
`AuthService.decode_token(token)` returns a `JwtClaims` instance so
the middleware accesses `claims.sub`, `claims.jti`, `claims.iss`,
`claims.aud`, `claims.pwd_sig` instead of `dict.get(...)`. User-only
fields (`username`, `role`, `must_change_password`, `pwd_sig`) are
optional so the same model serves both user tokens and CLI-minted
system tokens. `iat` and `exp` are `int` (epoch seconds); a `before`
validator coerces datetime values from the encode side.

A malformed token surfaces as a 401 through the middleware's existing
`_try_jwt_auth` failure path, with an additional
`SECURITY_AUTH_FAILED` log carrying `reason="jwt_claims_malformed"`
alongside the boundary helper's warning.

### Settings security config (`settings.security`)

The `import_security_config` controller routes the inbound
`data.config` dict through `parse_typed("settings.security", ...,
SecurityConfig)`. The export side already round-trips through
`SecurityConfig.model_dump` and never accepts external dict input.
`SecurityConfig` does not declare `extra="forbid"`; reject paths are
still the model's existing field validators (range checks, enum
coercion, cross-field constraints).

### WebSocket control messages (`ws.control`)

`WsControlMessage` is a `Discriminator("action")` union of four
variants:

- `WsAuthMessage{action: "auth", ticket: str}`: first-message ticket
  handshake.
- `WsSubscribeMessage{action: "subscribe", channels: tuple[str, ...],
  filters: dict[str, str] | None}` where `filters=None` leaves
  existing filters, `{}` clears them, and `{...}` replaces them.
- `WsUnsubscribeMessage{action: "unsubscribe", channels: tuple[str,
  ...]}`.
- `WsPingMessage{action: "ping"}`.

The shape mirrors the typed contract in
`web/src/api/types/websocket.ts` (PR #1718); bump
`WS_PROTOCOL_VERSION` on both sides together for breaking payload
changes. Malformed control frames return the generic `Invalid control
message` envelope and the connection stays open; the legacy
per-error strings (`Unknown action`, `filters must be an object`)
are gone.

### Audit-chain payload (`audit_chain`)

`AuditChainEventPayload` mirrors the field set
`AuditChainSink.emit()` extracts from each `LogRecord`. The model is
called for validation only; the helper never replaces the dict that
goes into `json.dumps(payload, sort_keys=True, ensure_ascii=True,
default=str)`, so the chain hash is byte-stable across the migration.
Two pinning tests in `tests/unit/observability/test_audit_chain_boundary.py`
guard the byte layout against future drift:

- `test_golden_json_byte_stable` compares the `json.dumps` output
  against a hard-coded byte string.
- `test_golden_hash_matches` pins the SHA-256 of the same bytes.

Regenerating either constant requires explicit reviewer sign-off
because a chain-hash change invalidates every previously-signed
audit entry.

### A2A JSON-RPC (`a2a.jsonrpc`)

`parse_rpc_params(rpc_request)` merges the envelope `method` into the
params dict (envelope wins on conflict, blocking peers that smuggle a
`method` key inside `params`) and routes through `parse_typed` against
the `A2ARpcParams` `TypeAdapter`. Variants:

- `A2AMessageSendParams` for `message/send`
- `A2ATaskGetParams` for `tasks/get`
- `A2ATaskCancelParams` for `tasks/cancel`

The wire shape is unchanged; the gateway still maps the re-raised
`ValidationError` to `JsonRpcErrorData(-32602, "Invalid params")`.

### MCP tool args (`mcp.tool`)

The MCP invoker validates `arguments` against each tool's declared
`args_model` through `parse_typed("mcp.tool", arguments, args_model)`
before dispatch. A malformed payload returns the
`ArgumentValidationError` / `domain_code=invalid_argument` envelope
on the wire. Tools without an `args_model` fall through to the
deepcopy path and continue to validate via `common_args` helpers in
the handler; this gate fires whenever a tool opts into typed args.

### Provider tool-call extraction (`provider.tool_call`)

LiteLLM provider drivers return tool-call payloads in heterogeneous
shapes: some completions parse to plain dicts, others surface objects
with attribute access (`item.function.arguments`). The provider layer
has no control over the upstream payload shape, so this boundary is
deliberately **lenient**: it does NOT run `parse_typed`. Instead,
`extract_tool_calls` (`src/synthorg/providers/drivers/mappers.py:131`)
walks the raw list and rescues whatever it can.

- Wire shape: `list[dict] | list[object]` (or `None` for completions
  that emitted no tool calls).
- Field access: `_get(item, "id"/"function", ...)` uses
  `dict.get` for mappings and `getattr` for objects; the helper
  centralises the lenient access so each call site does not branch.
- Failure modes:
  - Missing `function` block: skip the entry; emit
    `provider.tool_call.missing_function` warning with
    `item_type=type(item).__name__`.
  - Empty `id` or `name`: skip the entry; emit
    `provider.tool_call.incomplete` warning carrying whatever fields
    were recoverable.
  - Malformed `arguments` JSON: fall back to `{}` so the handler can
    apply its own validation (the alternative, rejecting the entire
    completion, would discard one good tool call because a sibling
    arrived malformed).
- Why lenient: provider variability dominates. A strict
  `parse_typed` here would surface a hard 5xx on every novel
  upstream shape, blocking the whole completion path. The warning
  logs preserve observability without coupling the wire contract to
  any single provider.

Each skipped entry is logged so a provider regression surfaces in
the event stream rather than disappearing silently. The handler that
consumes the returned `tuple[ToolCall, ...]` re-validates field
shape via the typed Pydantic `ToolCall` model.

### Webhook payload envelope (`webhook.payload`)

External webhook providers send arbitrary JSON keys (each integration
has its own schema). The boundary uses a Pydantic model with
`ConfigDict(extra="allow")` to enforce envelope shape only:

```python
class WebhookEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
```

(`src/synthorg/api/controllers/_webhooks_wiring.py:39`).

- Wire shape: any JSON object. Arrays, scalars, and non-JSON bodies
  are rejected at `parse_typed("webhook.payload", ...)` time and
  surface as HTTP 400.
- Provider keys flow through unchanged via `extra="allow"`. The
  controller routes the typed envelope to the integration-specific
  handler, which validates the inner payload against its own schema.
- Why `extra="allow"` and NOT `extra="forbid"`: flipping the config
  would break every integration the moment a provider added a new
  optional field. `frozen=True` still prevents mutation; the only
  relaxation is on unknown-key rejection.

The envelope-only validation closes the silent `{"raw": ...}`
fallback the controller carried before typed boundaries existed: a
non-object payload now fails fast instead of routing as a
single-key dict.

### MCP tool-execution dual paths (`mcp.tool.dual_path`)

The MCP invoker (`src/synthorg/meta/mcp/invoker.py:149`) routes tool
arguments through one of two validation paths depending on whether
the tool declares an `args_model`:

```python
if tool_def.args_model is not None:
    # Typed-args path.
    validated = parse_typed("mcp.tool", arguments, tool_def.args_model)
    handler_arguments = deepcopy(validated.model_dump(mode="python"))
else:
    # Per-field path.
    handler_arguments = deepcopy(arguments)
```

- **Typed-args path** (`args_model` declared): the raw dict goes
  through `parse_typed`, which emits
  `api.boundary.validation_failed` on rejection and re-raises. The
  invoker catches the `PydanticValidationError`, records
  `record_mcp_handler_outcome(outcome="validation_error", ...)`,
  and returns an `ArgumentValidationError` envelope with
  `domain_code=invalid_argument` to the client. The validated
  `model_dump(mode="python")` is deep-copied so handlers receive a
  fresh mutable dict.
- **Per-field path** (no `args_model`): the raw dict is deep-copied
  unchanged and handed to the handler. The handler validates each
  field through `common_args` helpers (`require_arg`,
  `require_non_blank`, `require_dict`, etc. in
  `src/synthorg/meta/mcp/handlers/common_args.py`), which raise
  `ArgumentValidationError` directly on missing or malformed
  inputs.

Both paths converge on the same wire envelope
(`domain_code=invalid_argument` on validation failure) and the same
observability surface (`MCP_SERVER_INVOKE_FAILED` warning,
`record_mcp_handler_outcome` with the `validation_error` outcome).
The typed-args path provides typed validation at construction time;
the per-field path provides field-level validation at call time.
Validation surface area is equivalent; opting a tool into
`args_model` is a code-quality refactor, not a security upgrade.

Pre-mapping shape check: the typed-args path rejects non-mapping
payloads (`isinstance(raw_arguments, dict)`) before invoking
`parse_typed`. A JSON array would otherwise survive `dict(...)`
coercion and reach Pydantic, broadening the contract beyond what
MCP expects.

## Lint guard (Phase 3)

`scripts/check_boundary_typed.py` walks the six registered functions
above and confirms each one still calls `parse_typed`. A regression
that drops the call (refactor, rename, accidental removal) fails the
gate before push.

The guard is wired into `.pre-commit-config.yaml` at the pre-push
stage and into the CI `Lint` job. Per-line opt-out is `# lint-allow:
boundary-typed -- <reason>` on the function def line.

## Adding a new boundary

1. Define a frozen Pydantic model (or a `TypeAdapter` for a
   discriminated union) under the relevant module.
2. Call `parse_typed("<dotted.label>", raw, Model)` at the entry
   point. The boundary label MUST be a string literal; the
   `LiteralString` type erases any runtime-derived value.
3. Translate the re-raised `ValidationError` into your boundary's
   native error envelope (HTTP, MCP, WS close code, JSON-RPC error,
   audit log). Do not swallow.
4. Add a `(file, function, label)` tuple to
   `_REGISTERED_BOUNDARIES` in `scripts/check_boundary_typed.py` and
   widen the `files:` pattern in the `boundary-typed` hook of
   `.pre-commit-config.yaml` to include the new file.
5. Add a row to the table above; add a per-boundary subsection
   below explaining wire shape, error envelope translation, and any
   stability constraints.
6. Cover the boundary with a `tests/unit/<area>/test_*_boundary.py`
   file asserting (a) accepted typed input, (b) rejected extra keys
   or missing-required, (c) wire-shape round-trip where applicable,
   and (d) `api.boundary.validation_failed` log emission.
