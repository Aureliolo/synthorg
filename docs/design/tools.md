---
title: Tools & Capabilities
description: Tool categories, concurrent execution model, layered sandboxing, MCP integration, progressive disclosure, action types, access levels, and approval workflow.
---

# Tools & Capabilities

Agents act on the world through tools. SynthOrg defines a pluggable tool system with 15+ categories (file system, git, web, database, terminal, sandbox, MCP bridge, analytics, communication, design, headless browser, governed external data access, virtual desktop), layered sandboxing (subprocess for low-risk, Docker for high-risk, Kubernetes for future multi-tenant), MCP server integration, and a progressive-disclosure model that limits the surface an agent sees to what its role and autonomy tier permit.

## Tool Categories

| Category | Tools | Typical Roles |
|----------|-------|---------------|
| **File System** | Read, write, edit, list, delete files | All developers, writers |
| **Code Execution** | Run code in sandboxed environments | Developers, QA |
| **Version Control** | Git operations, PR management | Developers, DevOps |
| **Web** | HTTP requests, web scraping, search | Researchers, analysts |
| **Database** | Query, migrate, admin | Backend devs, DBAs |
| **Terminal** | Shell commands (sandboxed) | DevOps, senior devs |
| **Design** | Image generation, mockup tools | Designers |
| **Communication** | Email / notification dispatcher tools (the Slack notification sink lives here; agent-invocable Slack access is the `chat_*` tools under External Data) plus `delegate_and_await`, the blocking sub-agent delegation tool that runs a child Task inline and returns its transcript (gated on a wired `SubAgentRunner`) | PMs, executives |
| **Analytics** | Metrics, dashboards, reporting | Data analysts, CFO |
| **Deployment** | CI/CD, container management | DevOps, SRE |
| **Memory** | Search memory, recall by ID | All agents (tool-based strategy) |
| **Browser** | Headless Playwright + Chromium: navigate, screenshot, SSIM diff, axe accessibility scan, full spec, direct WebStorage read/write (`storage_get`/`storage_set`/`storage_remove`/`storage_clear`), and WebAuthn passkey handling via a virtual authenticator (`webauthn_install`/`webauthn_create_credential`/`webauthn_list_credentials`/`webauthn_delete_credential`). The `url` field is restricted to `http`/`https` and rejects link-local / cloud-metadata hosts (169.254.169.254, `metadata.google.internal`); local files use the workspace-scoped `path` field. Loopback and private addresses stay allowed so the in-sandbox app-under-test is reachable. Session state (cookies, localStorage, passkeys) persists across calls (see [Browser Session State](#browser-session-state-webstorage--webauthn)) | QA, frontend devs, agents validating web deliverables |
| **External Data** | Governed external API/data access through a configured connection: credentials brokered from the connection catalog, egress constrained to the connection host, and sensitive/write calls gated to approval. The generic `external_api` tool adds a full SSRF policy + DNS pinning + per-connection rate limiting on top. The first-party **forge tools** (`forge_repo`, `forge_issue`, `forge_pull_request`, `forge_ci`) give the software-build org real hands on a repository (read repo/file, open/comment issues, open/comment/review/merge PRs, read CI runs; `forge_ci` is GitHub-only even on a bound Forgejo connection) and the **chat tools** (`chat_messages`, `chat_directory`) drive the operator control-plane channel (egress pinned to the platform host, e.g. `slack.com`, by construction). All are vendor-neutral (the concrete forge/chat provider is selected by the bound connection's type); reads on a `sensitive` connection and every write route through the identity-bound approval flow | Agents consuming third-party APIs, driving a forge, or messaging the operator while building deliverables |
| **Desktop** | Virtual desktop (Xvfb + xdotool + scrot in a container): launch a GUI app, click/type/press-keys/scroll, capture screenshots | QA, frontend devs, agents validating GUI deliverables |
| **MCP Servers** | Any MCP-compatible tool | Configurable per agent |

## Tool Execution Model

When the LLM requests multiple tool calls in a single turn, `ToolInvoker.invoke_all` executes
them **concurrently** using `asyncio.TaskGroup`. An optional `max_concurrency` parameter
(default unbounded) limits parallelism via `asyncio.Semaphore`. Recoverable errors are captured
as `ToolResult(is_error=True)` without aborting sibling invocations. Non-recoverable errors
(`MemoryError`, `RecursionError`) are collected and re-raised after all tasks complete (bare
exception for one, `ExceptionGroup` for multiple).

**Permission checking** follows a priority-based system:

1. `get_permitted_definitions()` filters tool definitions sent to the LLM; the agent only
   sees tools it is permitted to use
2. At invocation time, denied tools return `ToolResult(is_error=True)` with a descriptive
   denial reason (defence-in-depth against LLM hallucinating unpresented tools)

Resolution order: denied list (highest) > allowed list > access-level categories > deny (default).

## Tool Sandboxing

Tool execution uses a **layered sandboxing strategy** with a pluggable `SandboxBackend`
protocol. The default configuration uses lighter isolation for low-risk tools and stronger
isolation for high-risk tools.

### Sandbox Backends

| Backend | Isolation | Latency | Dependencies | Status |
|---------|-----------|---------|--------------|--------|
| `SubprocessSandbox` | Process-level: env filtering (allowlist + denylist), restricted PATH (configurable via `extra_safe_path_prefixes`), workspace-scoped cwd, timeout + process-group kill, library injection var blocking, explicit transport cleanup on Windows | ~ms | None | Implemented |
| `DockerSandbox` | Container-level: keep-alive container reused per the configured lifecycle strategy (`per-agent` default; `per-call` for maximum isolation), mounted workspace, no network (default) or sidecar-based host:port allowlist (dual-layer DNS + DNAT transparent proxy), resource limits (CPU/memory/time) | ~1-2s on first acquire; reused warm thereafter | Docker | Implemented |
| `K8sSandbox` | Pod-level: per-agent containers, namespace isolation, resource quotas, network policies | ~2-5s | Kubernetes | Planned |

???+ note "Default Layered Sandbox Configuration"

    ```yaml
    sandboxing:
      default_backend: "subprocess"        # subprocess, docker, k8s
      overrides:                           # per-category backend overrides
        file_system: "subprocess"          # low risk -- fast, no deps
        git: "subprocess"                  # low risk -- workspace-scoped
        web: "docker"                      # medium risk -- needs network isolation
        code_execution: "docker"           # high risk -- strong isolation required
        terminal: "docker"                 # high risk -- arbitrary commands
        database: "docker"                 # high risk -- data mutation
        browser: "docker"                  # opt-in -- Playwright + Chromium image
        desktop: "docker"                   # opt-in -- Xvfb + xdotool + scrot image
      subprocess:
        timeout_seconds: 30
        workspace_only: true               # restrict filesystem access to project dir
        restricted_path: true              # strip dangerous binaries from PATH
      docker:
        image: "synthorg-sandbox:latest" # pre-built image with common runtimes
        network: "none"                    # no network by default
        network_overrides:                 # category-specific network policies
          database: "bridge"               # database tools need TCP access to DB host
          web: "bridge"                    # web tools need outbound HTTP; no inbound
        allowed_hosts: []                  # allowlist of host:port pairs (TCP only)
        dns_allowed: true                  # allow outbound DNS when allowed_hosts restricts network
        loopback_allowed: true             # allow loopback traffic in restricted network mode
        memory_limit: "512m"
        cpu_limit: "1.0"
        timeout_seconds: 120
        pids_limit: 64                     # PID cap (main container) -- guards against fork-bomb runaways
        tmpfs_size: "64m"                  # tmpfs mounted at /tmp in the main container
        sidecar_pids_limit: 32             # PID cap for the stdio sidecar helper
        sidecar_tmpfs_size: "8m"           # tmpfs for the stdio sidecar helper
        mount_mode: "ro"                   # read-only by default
        auto_remove: true                  # remove the container once its lifecycle strategy tears it down
      k8s:                                 # planned -- per-agent pod isolation
        namespace: "synthorg-agents"
        resource_requests:
          cpu: "250m"
          memory: "256Mi"
        resource_limits:
          cpu: "1"
          memory: "1Gi"
        network_policy: "deny-all"         # default deny, allowlist per tool
    ```

Per-category backend selection is implemented in `tools/sandbox/factory.py` via three functions:
`build_sandbox_backends` (instantiates only the backends referenced by config),
`resolve_sandbox_for_category` (looks up the correct backend for a `ToolCategory`), and
`cleanup_sandbox_backends` (parallel cleanup with error isolation). The tool factory
(`build_default_tools_from_config`) wires tool categories. Core tools
(`FILE_SYSTEM`, `VERSION_CONTROL`, web, etc.) are part of the default toolset
and always registered. The
auxiliary categories `DESIGN`, `COMMUNICATION`, `EXTERNAL_DATA`, and `ANALYTICS`
are opt-in: tools are only registered when the corresponding config section is
present, and some individual tools additionally require a runtime dependency (e.g.
image tools require an `ImageProvider`, notification tools require a dispatcher,
the `forge_*`/`chat_*` tools require a bound connection and an enabling setting,
analytics query/metric tools require a provider or sink).

### MCP stdio server sandboxing

`ToolCategory.MCP` is **not** routed through `resolve_sandbox_for_category`.
A stdio MCP server is launched as a subprocess (`npx -y <package>@<version>`),
so its isolation is a bespoke launch-rewrite (`tools/mcp/sandbox.py`,
`wrap_stdio_in_sandbox`) that runs the server via `docker run -i`, letting the
MCP protocol flow over the container's stdio while the server runs under
`--cap-drop=ALL`, `--security-opt=no-new-privileges`, a read-only rootfs,
`NPM_CONFIG_IGNORE_SCRIPTS`, and cpu/memory/pid limits. This mechanism is
parallel to, not part of, the per-category `SandboxBackend` selection above,
and is controlled by its own `tools.mcp_sandbox_*` settings (on by default,
fail-secure to on). D16 classifies MCP with the high-risk Docker-required
categories; this is how that requirement is met for the stdio transport.

### Image generation

Image generation is a native provider capability, not a standalone subsystem.
A model advertises it through the `supports_image_generation` capability flag
(`ModelCapabilities` / `ModelMetadata`), populated from the provider preset or
the upstream model database, and surfaced in the dashboard model-management
view. The provider's `generate_image` capability (an `ImageGenerationMixin`
layered onto the completion driver, kept out of `BaseCompletionProvider` for its
size budget) runs the call through the same retry, rate-limit, and
cost-recording path as chat completion. Per-image cost is billed through a
`cost_per_image` model field under the `image_generation` call category: the
tool invoker opens a `cost_recording_scope` around the `image_generator` tool
(which declares that category), so a priced image model attributes spend to the
agent/task exactly like a completion.

The design `image_generator` tool routes through this layer via a
`ProviderImageProvider` adapter that satisfies the `ImageProvider` seam. Two
settings gate it, both off by default: `design.image_generation_enabled` and
`design.image_model` (a model reference the operator selects from connected
models). Connecting a provider whose preset ships an image model (for example
OpenAI or Gemini) makes that model selectable; the scripted provider ships an
offline, deterministic image model for tests and air-gapped installs.

Generated bytes are persisted through the design-asset store rooted at
`design_tools.asset_storage_path` (a durable, path-traversal-guarded filesystem
store, or an in-memory store when the path is unset), and can then be listed and
retrieved through the `asset_manager` tool. This is a separate seam from the
generic `ArtifactStorageBackend` used for workspace deliverables: design assets
carry their own id/metadata-sidecar lifecycle and are queried by the design
tools, not the task-artifact pipeline.

**Network posture.** An image call is a provider call: egress leaves the API
process to the provider using credentials brokered by the connection catalog,
exactly like a chat completion. The Docker sandbox default `network: "none"` is
unaffected because the design tool and the provider run in the API process, not
inside a sandbox container. There is no separate egress allowlist for image
generation; it inherits the provider connection posture. Residual gap: an
operator who selects a hosted image model accepts that provider outbound
egress, the same trade-off as enabling any hosted completion model.

Docker is optional; only required when code execution, terminal, web, database, or browser
tools are enabled. File system and git tools work out of the box with subprocess isolation.
This keeps the local-first experience lightweight while providing strong isolation where it
matters.

Docker MVP uses `aiodocker` (async-native) with a pre-built image
(Python 3.14 + Node.js LTS + basic utils, <500MB). If Docker is unavailable, the framework
fails with a clear error for any tool category whose configured backend is Docker;
low-risk categories (file_system, git) continue to run via subprocess
([Decision Log](../architecture/decisions.md) D16).

### Container Log Shipping

`DockerSandbox` collects structured logs from both sandbox and sidecar containers
before removal and ships them through the backend's observability pipeline.
Sidecar JSON stdout is parsed line-by-line; malformed lines are skipped.
Sandbox stdout/stderr are shipped alongside the sidecar entries. All shipped
events carry correlation context (`agent_id`, `session_id`, `task_id`,
`request_id`) injected via structlog contextvars, and the same IDs are set as
`SYNTHORG_AGENT_ID`, `SYNTHORG_SESSION_ID`, `SYNTHORG_TASK_ID`,
`SYNTHORG_REQUEST_ID` environment variables in both containers so
container-side logs can self-correlate.

`SandboxResult` includes optional Docker-specific fields: `container_id`,
`sidecar_id`, `sidecar_logs`, `agent_id`, and `execution_time_ms`.  These
default to `None`/empty for non-Docker backends.

Log shipping is failure-tolerant (errors are logged at debug level, never
propagated) and bounded by `ContainerLogShippingConfig.collection_timeout_seconds`
and `max_log_bytes`.  By default only metadata (sizes, counts, timing) is
shipped; raw stdout/stderr/sidecar payloads require explicit opt-in via
`ship_raw_logs=True` to prevent secrets from bypassing key-name-based
redaction. Configuration lives on `LogConfig.container_log_shipping`
(default: enabled).

!!! info "Scaling Path"

    In a future Kubernetes deployment, each agent can run in its own pod via
    `K8sSandbox`. At that point, the layered configuration becomes less relevant; all tools
    execute within the agent's isolated pod. The `SandboxBackend` protocol makes this
    transition seamless.

### Sandbox Lifecycle Strategies

Container lifecycle isolation (when to create, reuse, or destroy sandbox containers)
is configurable via the pluggable `SandboxLifecycleStrategy` protocol
(`src/synthorg/tools/sandbox/lifecycle/protocol.py`). Three built-in strategies control
the trade-off between resource efficiency and isolation:

| Strategy | Behaviour | Use case |
|----------|-----------|----------|
| `per-agent` (default) | One persistent container per agent; destroyed after a configurable grace period (default 30s) when the agent stops | Development, trusted environments |
| `per-task` | New container per task; destroyed immediately on task completion | Production, medium isolation |
| `per-call` | New container per tool invocation; destroyed immediately (current ephemeral behaviour) | High-security, maximum isolation |

Strategy selection via `sandboxing.docker.lifecycle.strategy` in `SandboxingConfig`.
The sidecar container shares the sandbox container's lifetime (created and destroyed
together, since they share a network namespace).

The configured default is `per-agent` (the `strategy` field default in
`SandboxLifecycleConfig`); the table above is authoritative. The strategy is
constructed at boot (`workers/runtime_builder`) with the application clock and
injected into `DockerSandbox` via the sandbox factory. Each tool call runs as
a `docker exec` inside a long-lived idle container (`tail -f /dev/null`
entrypoint) the strategy acquires; per-agent and per-task reuse the container
across calls while per-call destroys it immediately after the single exec. The
lifecycle owner is resolved from an explicit `owner_id`, else the structlog
correlation context (`agent_id` for per-agent, `task_id` for per-task). The
per-call degradation below is a per-invocation safety fallback, not a change
of the configured default: when a reuse strategy cannot derive an owner for a
given call, that single call degrades to ephemeral per-call behaviour while
the configured strategy stays in force for calls that can resolve an owner. `AgentEngineExecutionService` releases the owner at the
task boundary (per-task destroys immediately; per-agent starts the grace
timer so a subsequent task for the same agent within the window re-acquires
the warm container); `DockerSandbox.cleanup()` destroys all strategy-owned
containers via `cleanup_all()`. Containers carry the `synthorg.managed=true`
label so the reconciliation pass reclaims any orphaned on an unclean exit.

## Virtual Desktop & Vision Verification

For GUI deliverables an agent must SEE and operate the running app, not just
unit-test it. The **desktop tool** (`tools/desktop/`) drives a headless X session
inside the existing `DockerSandbox`: it launches a windowed GUI app, injects
pointer / keyboard input via `xdotool`, and captures screenshots via `scrot`. The
session is stateful across calls because the per-agent lifecycle keeps the warm
container (Xvfb + the running app) alive between tool invocations; a `per-call`
reset surfaces as `DesktopAppNotRunningError` rather than a silent empty capture.

The session bring-up is pluggable behind a `DesktopDriver` protocol + factory
(`tools/desktop/driver/`): `xvfb` (the deterministic default: Xvfb + xdotool +
scrot) and `vnc` (adds an x11vnc observation channel). The protocol leaves room
for a future Windows-container / Wayland driver without reworking the tool. The
driver targets Linux-renderable GUI toolkits (Qt / Tk / GTK / Electron / X11);
the desktop-capable image is built from `docker/desktop/Dockerfile`. Screenshots
are written under `<workspace>/.synthorg/desktop/screenshots/` with a sha256, so
they are durable provenance on disk (never in the database).

The screenshots feed the **vision verifier** quality gate (the UI cousin of the
red-team gate); see [Verification & Quality](verification-quality.md).

## Browser Session State: WebStorage & WebAuthn

The browser tool exposes two capability families that carry state between
separate tool calls: direct WebStorage access and WebAuthn passkey handling.
Because each call launches a fresh Chromium in the sandbox, that state is
persisted in the mounted workspace under a **per-owner** directory
(`<workspace>/.synthorg/browser/state/<owner>/`) so one agent's session state
is never visible to a different agent. Two files live there:

- **`storage_state.json`**: a Playwright storage-state snapshot (cookies plus
  per-origin localStorage). Loaded into the context on every call and re-saved
  after any navigation, so a `storage_set` followed by a later `storage_get`
  against the same origin observes the write, and an authenticated cookie jar
  survives across calls. `sessionStorage` is deliberately per-call: it is
  session-scoped by definition, and a fresh browser launch is a fresh session.
- **`webauthn_credentials.json`**: the virtual-authenticator credential
  keystore.

**WebStorage.** Reads name an explicit key; there is no whole-store dump, so a
page's tokens or embedded secrets are never returned wholesale into the
model-facing result. Written values are size-capped. Storage operations
navigate to the target origin first (WebStorage is per-origin).

**WebAuthn.** The tool drives a Playwright virtual authenticator: no real
hardware key is involved. `webauthn_create_credential` generates a discoverable
passkey; the credential is re-seeded into the authenticator at the start of
every subsequent call, so `webauthn_list_credentials` and
`webauthn_delete_credential` act on the persisted set, and a
`navigator.credentials.get()` ceremony triggered by a page during a later
navigation is answered automatically. Passkeys a page registers itself during
browsing are synced back into the keystore.

**Secret-handling posture (SEC-1).** A virtual credential's **private key**
never reaches the model-facing surface: it is stripped from every tool result
and lives only in the workspace keystore file, keyed by credential id, at the
same trust level as screenshots and baselines (on disk in the workspace, never
in the database, never in LLM context or persisted conversation history). The
sandbox re-seeds the authenticator from that host-side keystore by reference,
so the key material round-trips host-side only. Per-owner isolation of the
state directory is the boundary that keeps one agent's cookies and passkeys out
of reach of any other agent.

## Git Clone SSRF Prevention

The `git_clone` tool validates clone URLs against SSRF attacks via hostname/IP
validation with async DNS resolution (`git_url_validator` module). All resolved
IPs must be public; private, loopback, link-local, and reserved addresses are
blocked by default. A configurable `hostname_allowlist` lets legitimate internal
Git servers bypass the private-IP check.

**TOCTOU DNS rebinding mitigation** closes the gap between DNS validation and
`git clone`'s own resolution:

- **HTTPS URLs:** Validated IPs are pinned via `git -c http.curloptResolve=host:port:ip`
  (git >= 2.37.0; sandbox ships git 2.39+), so git uses the same addresses the validator checked.
- **SSH / SCP-like URLs:** A second DNS resolution runs immediately before execution;
  if the re-resolved IP set is not a subset of the validated set, the clone is blocked.
- **Literal IP URLs:** Immune (no DNS resolution occurs).

Both mitigations are configurable via `GitCloneNetworkPolicy.dns_rebinding_mitigation`
(default: enabled). Disable for hosts behind CDNs or geo-DNS where resolved IPs
legitimately vary between queries. For full defence-in-depth, combine with
network-level egress controls (firewall, HTTP CONNECT proxy) or container
network isolation (see Tool Sandboxing above).

## MCP Integration

External tools are integrated via the **Model Context Protocol** (MCP).

- **SDK:** Official `mcp` Python SDK, pinned version. A thin `MCPBridgeTool` adapter layer
  isolates the rest of the codebase from SDK API changes
  ([Decision Log](../architecture/decisions.md) D17)
- **Transports:** stdio (local/dev) and Streamable HTTP (remote/production). Deprecated SSE
  is skipped.
- **Result mapping:** Text blocks concatenate to `content: str`; image/audio use placeholders
  with base64 in metadata; `structuredContent` maps to `metadata["structured_content"]`;
  `isError` maps 1:1 to `is_error`
  ([Decision Log](../architecture/decisions.md) D18)

### SynthOrg MCP Tool Surface

SynthOrg exposes its own MCP server offering <!--RS:mcp_tools-->247<!--/RS-->
tools across <!--RS:mcp_domains-->22<!--/RS--> domain
modules (agents, analytics, approvals, brain, budget, charter, cockpit,
communication, coordination, docs, infrastructure, integrations, knowledge,
memory, meta, organisation, quality, research, security, signals, tasks,
workflows).
Tool definitions are classified
by capability action via the
`read_tool` / `write_tool` / `admin_tool` builders
(`src/synthorg/meta/mcp/tool_builder.py`); only the `admin_tool` subset is
destructive and subject to the guardrail triple. Every tool is handled by an
async function in `src/synthorg/meta/mcp/handlers/<domain>.py`; handlers shim
onto the existing service layer rather than reimplementing business logic.

**Handler Protocol.** Every handler implements
`ToolHandler.__call__(*, app_state, arguments: dict[str, Any], actor: AgentIdentity | None = None) -> str`
(see `src/synthorg/meta/mcp/handler_protocol.py`). The `actor` argument threads the
calling agent identity through the invoker so destructive-op guardrails can
enforce attribution; handlers that don't care about identity accept it and
ignore it.

**Typed args (#1611 Phase 4).** Each MCP tool registration optionally carries
an `args_model: type[BaseModel]` (see `MCPToolDef.args_model`). When set, the
invoker validates the raw `arguments` dict against the Pydantic model **before**
dispatching to the handler; validation failures short-circuit to a typed
`ArgumentValidationError` envelope without ever invoking the handler. Handlers
therefore receive a structurally-validated dict and can either access fields
directly (the model guarantees presence + type) or re-validate locally for
typed access (`args_model.model_validate(arguments)`). Tools without
`args_model` (legacy / dynamic shapes such as `MCPBridgeTool`) continue using
the manual `common_args` validators inside the handler body.

**Envelope Contract.** Every handler returns a JSON string. Success envelope
(data is always present, pagination appears only on list/collection responses):

```json
{
  "status": "ok",
  "data": {"example": "payload"},
  "pagination": {"total": 100, "offset": 0, "limit": 50}
}
```

> Note: the example shows the **MCP-layer** ``PaginationMeta`` (defined in
> ``synthorg.meta.mcp.handlers.common``), which intentionally retains the
> legacy ``total``/``offset`` shape because MCP handlers slice
> already-materialised sequences. The HTTP API uses a separate cursor-only
> envelope (``synthorg.api.dto.PaginationMeta`` with
> ``{limit, next_cursor, has_more}``); see persistence.md.

Handler-caught error envelope (domain_code identifies the error class for
programmatic dispatch):

```json
{
  "status": "error",
  "error_type": "ArgumentValidationError",
  "message": "Argument 'approval_id' missing or not a non-blank string",
  "domain_code": "invalid_argument"
}
```

Shared handler infrastructure lives in three sibling modules under
`src/synthorg/meta/mcp/handlers/`. The split keeps each module focused
on one concern and below the 800-line file ceiling.

**`common.py`**: response envelopes, pagination output, guardrails,
placeholder factories:

- `ok(data, *, pagination=None)`: success envelope with optional
  `PaginationMeta` metadata (frozen Pydantic model, `allow_inf_nan=False`).
- `err(exc, *, domain_code=None)`: error envelope; `message` always goes
  through `safe_error_description(exc)` (SEC-1) and `domain_code` falls back
  to `exc.domain_code` when present.
- `not_supported(tool_name, reason)`: stable `status="error"` /
  `domain_code="not_supported"` envelope for a wired handler whose selected
  backend cannot perform the operation (e.g. a memory backend that does not
  support fine-tuning or checkpoints). Emits the `MCP_HANDLER_NOT_IMPLEMENTED`
  WARNING event so operators can alert on unsupported calls.
- `capability_gap(tool_name, reason)`: a wired handler whose backing service
  is absent in this deployment, or whose primitive does not yet expose the
  required method (a `None`-slice gap; see
  [unshipped-surface-inventory.md](../reference/unshipped-surface-inventory.md)
  for the classification). Identical wire envelope to `not_supported`
  (`domain_code="not_supported"`) but emits the dedicated
  `MCP_HANDLER_CAPABILITY_GAP` INFO event so ops telemetry distinguishes it
  from a backend that simply cannot perform the operation.
- `require_admin_guardrails(arguments, actor)`: single source of
  truth for the admin-op precondition triple: non-`None` `actor`,
  literal `confirm=True`, non-blank `reason`. Raises
  `GuardrailViolationError` with a typed `violation` code
  (`"missing_actor"` / `"missing_confirm"` / `"missing_reason"`).
- `paginate_sequence(seq, *, offset, limit, total=None)`: in-memory
  page slicing that returns `(page, PaginationMeta)`.
- `dump_many(models)`: batch Pydantic model serialisation to
  JSON-mode dicts.

**`common_args.py`**: argument validators/extractors. Every helper
raises `ArgumentValidationError` on bad input so handlers can convert
to a stable `err(...)` envelope without catching framework-specific
exceptions:

- `require_arg(arguments, key, ty)`: typed required-argument extraction
  (ruff `EM101`-safe).
- `require_non_blank(arguments, key)`: required non-blank string,
  whitespace-stripped.
- `get_optional_str(arguments, key)`: optional non-blank string;
  returns `None` when missing/empty.
- `require_dict(arguments, key, *, value_type=None, deep_copy=True)`:
  required dict argument; pass `value_type=str` for `dict[str, str]`
  validation. Defaults to deep-copying the input to decouple handler
  mutations from caller payload.
- `parse_time_window(arguments, *, until_required=True)`: ISO 8601
  since/until parsing with timezone-aware enforcement and
  `since < until` ordering.
- `parse_str_sequence(arguments, key)`: optional sequence-of-non-blank-strings.
- `coerce_pagination(arguments, *, default_limit=50)`: offset/limit
  parsing with strict bounds and explicit bool rejection. MCP tools
  default to 50; this is intentionally lower than the repository-layer
  `DEFAULT_LIST_LIMIT = 100` so paginated MCP responses stay terse for
  assistants.
- `actor_id(actor)` / `require_actor_id(actor)` / `actor_label(actor)`:
  actor identity helpers. Use `actor_id` for optional attribution,
  `require_actor_id` when attribution is mandatory (raises if
  unidentifiable), `actor_label` only for emit-only paths where a
  `"mcp-anonymous"` fallback is acceptable.

**`common_logging.py`**: the three handler-layer log helpers.
Module-scoped logger keyed at `synthorg.meta.mcp.handlers` so test
assertions see a single stable event source regardless of which domain
handler emitted the event:

- `log_handler_argument_invalid(tool, exc)`: caught
  `ArgumentValidationError`. Emits `MCP_HANDLER_ARGUMENT_INVALID` at
  WARNING.
- `log_handler_invoke_failed(tool, exc, **context)`: generic
  `Exception` from the service layer. `**context` carries optional
  correlation ids (e.g. `task_id=`, `decision_id=`); keys that would
  shadow the canonical event fields (`tool_name`, `error_type`,
  `error`, `event`, `log_level`) are rejected with `ValueError`.
- `log_handler_guardrail_violated(tool, exc)`: caught
  `GuardrailViolationError` from an admin-op precondition.
  Records only the typed `violation` code; the human message stays in
  the response envelope.

All three route exception messages through `safe_error_description`
(SEC-1) so secret-shaped fragments are scrubbed before reaching logs.

**Domain Codes.** Handlers set stable wire codes so callers can dispatch
programmatically: `invalid_argument`, `guardrail_violated`, `not_supported`,
`not_found`, `conflict` (e.g. active-checkpoint delete), plus any
domain-specific codes set via the `domain_code` kwarg on `err(...)`.

**Registry Immutability.** Each domain handler module exports an
`XXX_HANDLERS: Mapping[str, ToolHandler]` constant wrapped in
`MappingProxyType` to enforce read-only access. Each feature manifest
calls `mcp_descriptor(..., handlers=<loader>)` (the public keyword) to
pair its domain with a deferred zero-arg loader returning that constant;
`mcp_descriptor` stores it as the internal `handlers_factory` field on the
descriptor it returns. `build_handler_map()` in
`src/synthorg/meta/mcp/handlers/__init__.py` walks `discover_features()`,
invokes each feature's `handlers_factory()`, merges the maps, and raises
on a duplicate key across features.

**Schema-Level Validation.** Admin-op schemas in
`src/synthorg/meta/mcp/domains/*.py` enforce the `reason` field as a
non-whitespace string via `"minLength": 1` + `"pattern": r".*\S.*"`, and the
`confirm` field as literal `true` via JSON Schema `"enum": [true]`. Handler
guardrails run regardless so validation stays uniform once services come
online.

## Self-Extending Toolkit

The toolsmith lets the organisation extend its own MCP tool surface at runtime when it hits a recurring capability gap, governed end to end (autonomous detection, LLM authoring, the self-improvement guard chain, benchmark validation, and live registration into a layered tool registry). It has its own design page: [Toolsmith (Self-Extending Toolkit)](toolsmith.md). It is disabled by default (`meta.self_improvement` -> `tool_creation_enabled`).

## Progressive Tool Disclosure

When the tool inventory exceeds roughly thirty tools, loading every full definition into the LLM
context upfront becomes a major token tax. Progressive disclosure uses a three-level
hierarchy inspired by Google ADK's skill loading pattern:

| Level | Contents | When injected | Token cost |
|-------|----------|---------------|------------|
| **L1 metadata** | name, one-line description, category, cost tier | Always (system prompt) | ~100 tokens/tool |
| **L2 body** | full description, JSON Schema, examples, failure modes | On demand via `load_tool()` | <5K tokens/tool |
| **L3 resource** | markdown guides, code samples, example traces | Explicit via `load_tool_resource()` | Varies |

**Discovery tools** (always available regardless of agent access level):

- `list_tools()`: returns L1 metadata for all permitted tools
- `load_tool(tool_name)`: returns L2 body; marks tool as loaded in `AgentContext`
- `load_tool_resource(tool_name, resource_id)`: returns specific L3 resource

**Context injection:**

1. L1 metadata is injected into the system prompt for all permitted tools
2. Full `ToolDefinition` objects are sent via the provider API `tools` parameter
   only for loaded tools + discovery tools
3. L3 resources are never auto-injected; returned inline from `load_tool_resource`

**Auto-unload:** When `AgentContext.context_fill_percent` exceeds
`ToolDisclosureConfig.unload_threshold_percent` (default 80%), the oldest-loaded
L2 body is unloaded (FIFO by insertion order). L1 metadata remains.

**Configuration** (`ToolDisclosureConfig`):

- `l1_token_budget` (default 3000): max tokens for L1 metadata
- `l2_token_budget` (default 15000): max tokens for loaded L2 bodies
- `auto_unload_on_budget_pressure` (default `true`)
- `unload_threshold_percent` (default 80.0)

Cross-reference: MCP integration above is the external tool integration pattern;
progressive disclosure is the local analogue for managing context cost.

## Action Type System

Action types classify agent actions for use by autonomy presets (see [Security & Approval](security.md#autonomy-levels)),
SecOps validation, and tiered timeout policies
([Decision Log](../architecture/decisions.md) D1).

**Registry:** `StrEnum` for ~43 built-in action types (type safety, autocomplete, typos caught
by static type checking and config-load-time validation) + `ActionTypeRegistry` for custom
types via explicit registration. Unknown strings are rejected at config load time; a typo
in `human_approval` list silently meaning "skip approval" is a critical safety concern.

**Granularity:** Two-level `category:action` hierarchy. Category shortcuts expand to all
actions in that category (e.g., `auto_approve: ["code"]` expands to all `code:*` actions).
Fine-grained overrides are supported (e.g., `human_approval: ["code:create"]`).

**Taxonomy (41 leaf types):**

```text
code:read, code:write, code:create, code:delete, code:refactor
test:write, test:run
docs:write
vcs:read, vcs:commit, vcs:push, vcs:branch
deploy:staging, deploy:production
comms:internal, comms:external
budget:spend, budget:exceed
org:hire, org:fire, org:promote
db:query, db:mutate, db:admin
arch:decide
tool:create
memory:read
knowledge:ingest, knowledge:reindex
browser:navigate, browser:screenshot, browser:diff, browser:accessibility_scan, browser:spec
external_data:request
desktop:launch, desktop:click, desktop:type, desktop:key, desktop:screenshot, desktop:scroll
```

**Classification:** Static tool metadata. Each `BaseTool` declares its `action_type`. Default
mapping from `ToolCategory` to action type. Non-tool actions (`org:hire`, `budget:spend`) are
triggered by engine-level operations. No LLM in the security classification path.

## Tool Access Levels

???+ note "Tool Access Level Configuration"

    ```yaml
    tool_access:
      levels:
        sandboxed:
          description: "No external access. Isolated workspace."
          file_system: "workspace_only"
          code_execution: "containerized"
          network: "none"
          git: "local_only"

        restricted:
          description: "Limited external access with approval."
          file_system: "project_directory"
          code_execution: "containerized"
          network: "allowlist_only"
          git: "read_and_branch"
          requires_approval: ["deployment", "database_write"]

        standard:
          description: "Normal development access."
          file_system: "project_directory"
          code_execution: "containerized"
          network: "open"
          git: "full"
          terminal: "restricted_commands"

        elevated:
          description: "Full access; granting it requires human approval."
          file_system: "full"
          code_execution: "containerized"
          network: "open"
          git: "full"
          terminal: "full"
          deployment: true

        custom:
          description: "Per-agent custom configuration."
    ```

The `ToolPermissionChecker` implements two layers of enforcement: **category-level gating**
(each access level maps to permitted `ToolCategory` values) and **granular sub-constraints**
(`SubConstraintEnforcer`) checking file system scope, network mode, terminal access, git access,
code execution isolation, and approval requirements against each tool invocation. Per-agent
overrides can customise all six dimensions via `ToolPermissions.sub_constraints`.  K8s sandbox
backend integration is on the roadmap.

## See Also

- [Providers](providers.md): LLM abstraction and routing
- [Security & Approval](security.md): autonomy tiers, approval gates
- [Design Overview](index.md): full index
