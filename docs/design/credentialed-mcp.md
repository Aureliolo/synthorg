# Credentialed-tool MCP server

The credentialed-MCP server is a streamable-HTTP MCP endpoint mounted on
the Litestar API app that exposes a scoped, governed subset of the
credential-holding tools (forge / chat, with connections and remote-git
following the same pattern) to an embedded coding harness. It is the
second governance boundary behind the embedded [OpenHands loop](openhands-loop.md);
the first is the [LLM gateway](llm-gateway.md).

The design invariant is **credentials never enter the sandbox**. Tool
execution, credential brokering, the approval gate, the action signature
and the egress pin all run host-side in the API process; the harness only
ever sees tool *schemas* and already-fenced tool *results*.

## Boundary

Base forge/chat tools are agent-engine `BaseTool`s over
`GovernedConnectionTool`, not MCP handlers, and the in-process `meta/mcp`
machinery has no live network transport. This boundary adds one
(`api/mcp_gateway/controller.py`) at `POST /mcp-gateway/mcp`, reachable
only from the sandbox over the sidecar egress allowlist and authenticated
with the same per-run signed bearer as the gateway. It is excluded from
the session/bearer auth middleware for that reason.

## Tool set and capabilities

`api/mcp_gateway/tools.py` declares the credentialed tools as a static
tuple, each carrying a capability tag:

- **forge** (`forge:read` / `forge:write`): repo, issue, pull-request, CI.
- **chat** (`chat:read` / `chat:write`): messages, directory.

Each tool wraps the existing governed implementation
(`tools/forge/forge_tools.py`, `tools/chat/chat_tools.py`) rather than
re-implementing it.

## Scoping (per actor)

The bearer's capability list scopes visibility. `visible_tool_names`
resolves a tool with **denied name > allowed name > capability-pattern
match**, so an operator can widen by capability and still carve out an
individual tool by name. `tools/list` returns only visible schemas;
`tools/call` on a scoped-out name is a `ResourceNotFoundError` (invisible)
or `ForbiddenError` (visible but not permitted), never a silent success.

## Per-call governance (host-side)

`invoke_credentialed_tool` runs, in order:

1. **Scope check** against the actor's capabilities (above).
2. **Security pre-check**: the injected `security_pre_check` runs the
   SecOps pre-tool evaluation (rule engine + audit + escalation).
3. **Typed boundary**: `parse_typed` over the raw MCP `arguments` into the
   tool's args model; a bad shape is a typed `ValidationError`, never a
   crash.
4. **Governed dispatch**: the tool's `GovernedConnectionTool.execute`
   pipeline runs the `ConnectionApprovalGate` (an `ActionSignature`
   SHA-256-binds namespace + connection + operation + payload), parks a
   `PENDING` approval when unapproved, brokers the credential via the
   `ConnectionCatalog` host-side, builds a per-call client pinned to the
   connection `base_url` (a structural egress pin), and dispatches.
   Destructive tools additionally clear `require_admin_guardrails`
   (confirm + reason + actor) as their first statement.
5. **SEC-1 at source**: the tool output is wrapped with
   `wrap_untrusted(TAG_TOOL_RESULT, ...)` before it returns to the
   harness, so untrusted upstream content is fenced where it originates.

## Approval-parking UX

When the approval gate parks a call, the harness receives a
"requires approval" observation, **not** a failure. The correct harness
behaviour is to pause and re-issue the identical call after a human
approves; because the `ActionSignature` is deterministic over the same
arguments, the re-issued call matches the approved signature and proceeds.
This is a first-class flow, documented so the adapter treats it as
pause-and-retry rather than a terminal error.

## Protocol

`api/mcp_gateway/protocol.py::dispatch_mcp` handles `initialize`,
`tools/list` and `tools/call`. A tool error becomes an MCP `isError`
result (not a transport error); an unknown method is `-32601`; malformed
params are `-32602`. Batch requests are supported.

## Settings

Under the `tools` namespace, off by default, hot-reloadable:
`credentialed_mcp_enabled`, `credentialed_mcp_capabilities` (the
comma-separated default capability grant). It reuses the existing
forge/chat connection, timeout and read-limit settings. Enabling the
endpoint opens a credentialed egress path, so
`tools.credentialed_mcp_enabled` `false -> true` routes through the
confirm+reason+actor guardrail in `settings/write_governance.py`.

## Wiring

An auto-discovered feature registers `CredentialedMcpController` alongside
the gateway controller. The controller resolves its per-call context
(connection catalog, approval store, forge/chat connection names,
timeouts, security pre-check) from `app_state` at request time, so
`tools.*` toggles take effect on the next request with no restart. All
collaborators already exist on `app_state` (connection catalog via the
integrations slice, `approval_store_of`, `app_state.clock`); no new state
slice is introduced.

## Convention gate

`scripts/check_credentialed_mcp_governed.py` enforces that every
credentialed-MCP tool routes through the governed connection pipeline and
that `invoke_credentialed_tool` wraps outputs via `wrap_untrusted`.
