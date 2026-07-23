# Credentialed-tool MCP server

The credentialed-MCP server is a streamable-HTTP MCP endpoint mounted on
the Litestar API app that exposes a scoped, governed subset of the
credential-holding tools (forge / chat / deploy, with connections and
remote-git following the same pattern) to an embedded coding harness. It is the
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
(`api/mcp_gateway/controller.py`) at `POST /mcp-gateway/mcp`, so the full
mounted route (under the app's `/api/v1` prefix) is
`/api/v1/mcp-gateway/mcp`; the harness's MCP `base_url` is the app address
plus `/api/v1/mcp-gateway` (the runtime appends `/mcp`). It is reachable
only from the sandbox over the sidecar egress allowlist and authenticated
with the same per-run signed bearer as the gateway. It is excluded from
the session/bearer auth middleware for that reason.

## Tool set and capabilities

`api/mcp_gateway/tools.py` declares the credentialed tools as a static
tuple, each carrying a capability tag:

- **forge** (`forge:read` / `forge:write`): repo, issue, pull-request, CI.
- **chat** (`chat:read` / `chat:write`): messages, directory.
- **deploy** (`deploy:read` / `deploy:write`): `deploy_run` observes
  deployments (state, list, logs); `deploy_release` triggers one.

Each tool wraps the existing governed implementation
(`tools/forge/forge_tools.py`, `tools/chat/chat_tools.py`,
`tools/deploy/deploy_tools.py`) rather than re-implementing it.

### Risk classification

Not every write is destructive, and the distinction is load-bearing:

| Family | Reads | Writes | Destructive |
| --- | --- | --- | --- |
| forge | repo, CI | issue, pull request | no |
| chat | directory | messages | no |
| deploy | `deploy_run` | `deploy_release` | **yes** |

Opening an issue adds something reversible. A release **replaces what is
currently serving**, so `deploy_release` alone carries `_DESTRUCTIVE`, the
confirm + reason + actor guardrail, and a `deploy:staging` /
`deploy:production` action type rather than the shared `comms:external`.
That last point is a security boundary, not bookkeeping: every family
previously shared one action type, so an operator auto-approving
`comms:external` for chat would have auto-approved production deploys too.
Enforced by `check_governed_destructive_tools.py`.

`deploy_run` stays a read. It is capability-scoped, SecOps-screened,
egress-pinned and SEC-1 fenced, but it does not park an approval: an agent
that could not cheaply poll the release it just triggered could not react
to a failing one. An operator wanting reads gated too sets `sensitive` on
the connection, which makes the base pipeline gate every call.

Residual exposure to accept knowingly: build logs routinely echo
environment detail, and `deploy:read` grants access to them. Output is
fenced and truncated at `deploy_tools_max_log_chars`, but the grant is
what bounds who can read them.

### Deploy targets

Unlike forge and chat, deploy is **not** bound to one connection. An
organisation deploys to several targets, so `target` is a per-call
argument resolved against the operator's `deploy_tools_targets`
allowlist. An unlisted target is refused **before any credential is
brokered**.

Each target is one `ConnectionType.DEPLOY` connection whose `metadata`
declares its platform, project, and environment. The environment lives on
the record rather than in the arguments on purpose: it decides the action
type, so an agent able to assert it could route a production release
through a staging autonomy grant. The agent chooses *which* approved
target to use; the record decides how dangerous that target is. An absent
or unrecognised environment resolves to `production`, so a mislabelled
target is over-gated rather than treated as throwaway.

The client built for a target is bound to that project **and** that
environment, and the binding is enforced on what comes back, not only on
what goes out. A list read is filtered server-side by both, and a reply
that contradicts the filter is refused rather than returned. A by-id read
has no server-side filter to lean on and an identifier is quotable from an
earlier call, so `deploy_run`'s `get` verifies the returned record names
the bound project and target before surfacing it, and refuses when the
payload confirms neither. Build logs are the sharpest case: the platform's
events endpoint carries no ownership of its own, so a log read resolves the
deployment record first and only then fetches events. Without that a
staging-bound target could pull a production build log by id alone.

### Setup a human must finish

A deploy target needs out-of-band setup (an account, an API token, a
project). `integrations/connections/field_metadata.py` declares the DEPLOY
type's fields once, and both the Operator Console conversational
`CONFIGURE` flow and the dashboard connection form read that same
definition, with the token captured out of band so it never enters a
transcript. Setting a target up by talking to the organisation and setting
it up through the form are the same path, as with every other capability.

When a target is missing or half-configured the tools raise
`DeploySetupRequiredError` naming what a person must supply, distinct from
a plain not-found. That distinction is what lets the organisation *raise
the need* with a human instead of reporting an opaque failure.

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
2. **Security pre-check**: the controller wires a **fail-closed**
   `security_pre_check` that runs the SecOps pre-tool evaluation (rule
   engine + audit + escalation) via `SecOpsService.evaluate_pre_tool`; a
   non-`ALLOW` verdict denies the call, and with no active security
   governance every credentialed call is denied (the credentialed tools
   are unreachable until security is enabled).
3. **Typed boundary**: `parse_typed` over the raw MCP `arguments` into the
   tool's args model; a bad shape is a typed `ValidationError`, never a
   crash.
4. **Governed dispatch**: the tool's `GovernedConnectionTool.execute`
   pipeline runs the `ConnectionApprovalGate` (an `ActionSignature`
   SHA-256-binds namespace + connection + operation + payload), parks a
   `PENDING` approval when unapproved, brokers the credential via the
   `ConnectionCatalog` host-side, builds a per-call client pinned to the
   connection `base_url` (a structural egress pin), and dispatches.
   Destructive tools invoke `require_admin_guardrails` (the confirm,
   reason and actor triple) as the first statement of
   `_check_preconditions`, which runs **before** the gate: a call nobody
   could have authorised is refused outright rather than parked as an
   approval for a human to adjudicate.
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
comma-separated default capability grant), and `credentialed_mcp_base_url`
(the sandbox-reachable base URL the harness connects to, e.g.
`http://host.internal:8000/api/v1/mcp-gateway`; the runtime appends
`/mcp`). It reuses the existing forge/chat connection, timeout, and
read-limit settings, and the deploy family adds `deploy_tools_enabled`,
`deploy_tools_targets`, `deploy_tools_timeout_seconds` and
`deploy_tools_max_log_chars`.

Both the enable toggle and any *widening* of
`credentialed_mcp_capabilities` (a broader grant, e.g. `"" -> "*"` or
adding a `:write`) open a credentialed egress path, so they route through
the confirm+reason+actor guardrail in `settings/write_governance.py`.
`deploy_tools_enabled` and `deploy_tools_targets` are guarded the same
way: adding a target does not merely widen a permission, it makes a real
production destination reachable.

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
