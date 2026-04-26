---
title: Tools & Capabilities
description: Tool categories, concurrent execution model, layered sandboxing, MCP integration, progressive disclosure, action types, access levels, and approval workflow.
---

# Tools & Capabilities

Agents act on the world through tools. SynthOrg defines a pluggable tool system with 12+ categories (file system, git, web, database, terminal, sandbox, MCP bridge, analytics, communication, design), layered sandboxing (subprocess for low-risk, Docker for high-risk, Kubernetes for future multi-tenant), MCP server integration, and a progressive-disclosure model that limits the surface an agent sees to what its role, seniority, and autonomy tier permit.

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
| **Communication** | Email, Slack, notifications | PMs, executives |
| **Analytics** | Metrics, dashboards, reporting | Data analysts, CFO |
| **Deployment** | CI/CD, container management | DevOps, SRE |
| **Memory** | Search memory, recall by ID | All agents (tool-based strategy) |
| **MCP Servers** | Any MCP-compatible tool | Configurable per agent |

## Tool Execution Model

When the LLM requests multiple tool calls in a single turn, `ToolInvoker.invoke_all` executes
them **concurrently** using `asyncio.TaskGroup`. An optional `max_concurrency` parameter
(default unbounded) limits parallelism via `asyncio.Semaphore`. Recoverable errors are captured
as `ToolResult(is_error=True)` without aborting sibling invocations. Non-recoverable errors
(`MemoryError`, `RecursionError`) are collected and re-raised after all tasks complete (bare
exception for one, `ExceptionGroup` for multiple).

**Permission checking** follows a priority-based system:

1. `get_permitted_definitions()` filters tool definitions sent to the LLM -- the agent only
   sees tools it is permitted to use
2. At invocation time, denied tools return `ToolResult(is_error=True)` with a descriptive
   denial reason (defense-in-depth against LLM hallucinating unpresented tools)

Resolution order: denied list (highest) > allowed list > access-level categories > deny (default).

## Tool Sandboxing

Tool execution uses a **layered sandboxing strategy** with a pluggable `SandboxBackend`
protocol. The default configuration uses lighter isolation for low-risk tools and stronger
isolation for high-risk tools.

### Sandbox Backends

| Backend | Isolation | Latency | Dependencies | Status |
|---------|-----------|---------|--------------|--------|
| `SubprocessSandbox` | Process-level: env filtering (allowlist + denylist), restricted PATH (configurable via `extra_safe_path_prefixes`), workspace-scoped cwd, timeout + process-group kill, library injection var blocking, explicit transport cleanup on Windows | ~ms | None | Implemented |
| `DockerSandbox` | Container-level: ephemeral container, mounted workspace, no network (default) or sidecar-based host:port allowlist (dual-layer DNS + DNAT transparent proxy), resource limits (CPU/memory/time) | ~1-2s cold start | Docker | Implemented |
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
        auto_remove: true                  # ephemeral -- container removed after execution
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
(`build_default_tools_from_config`) wires tool categories.  Core tools
(`FILE_SYSTEM`, `VERSION_CONTROL`, web, etc.) are part of the default toolset
and always registered.  The
auxiliary categories `DESIGN`, `COMMUNICATION`, and `ANALYTICS` are opt-in: tools
are only registered when the corresponding config section is present, and some
individual tools additionally require a runtime dependency (e.g. image tools
require an ``ImageProvider``, notification tools require a dispatcher, analytics
query/metric tools require a provider or sink).

Docker is optional -- only required when code execution, terminal, web, or database tools are
enabled. File system and git tools work out of the box with subprocess isolation. This keeps
the local-first experience lightweight while providing strong isolation where it matters.

Docker MVP uses `aiodocker` (async-native) with a pre-built image
(Python 3.14 + Node.js LTS + basic utils, <500MB). If Docker is unavailable, the framework
fails with a clear error -- no unsafe subprocess fallback for code execution
([Decision Log](../architecture/decisions.md) D16).

### Container Log Shipping

`DockerSandbox` collects structured logs from both sandbox and sidecar containers
before removal and ships them through the backend's observability pipeline.
Sidecar JSON stdout is parsed line-by-line; malformed lines are skipped.
Sandbox stdout/stderr are shipped alongside the sidecar entries.  All shipped
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
redaction.  Configuration lives on `LogConfig.container_log_shipping`
(default: enabled).

!!! info "Scaling Path"

    In a future Kubernetes deployment, each agent can run in its own pod via
    `K8sSandbox`. At that point, the layered configuration becomes less relevant -- all tools
    execute within the agent's isolated pod. The `SandboxBackend` protocol makes this
    transition seamless.

### Sandbox Lifecycle Strategies

Container lifecycle isolation -- when to create, reuse, or destroy sandbox containers
-- is configurable via the pluggable `SandboxLifecycleStrategy` protocol
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

> **Status**: The lifecycle protocol, config, factory, and three strategy
> implementations are complete. Integration into `DockerSandbox.execute()` is
> in progress -- the `owner_id` parameter is accepted and the config field is
> wired, but the Docker backend does not yet dispatch to the lifecycle strategy.
> Until wired, all executions use the current per-call ephemeral behaviour.

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
legitimately vary between queries. For full defense-in-depth, combine with
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

SynthOrg exposes its own MCP server offering 200+ tools across 15 domains
(agents, tasks, workflows, approvals, budget, memory, quality, organization,
communication, coordination, analytics, integrations, infrastructure, signals,
meta). Tool definitions are classified by capability action via the
`read_tool` / `write_tool` / `admin_tool` builders
(`src/synthorg/meta/mcp/tool_builder.py`); only the `admin_tool` subset is
destructive and subject to the guardrail triple. Every tool is handled by an
async function in `src/synthorg/meta/mcp/handlers/<domain>.py`; handlers shim
onto the existing service layer rather than reimplementing business logic.

**Handler Protocol.** Every handler implements
`ToolHandler.__call__(*, app_state, arguments, actor: AgentIdentity | None = None) -> str`
(see `src/synthorg/meta/mcp/invoker.py`). The `actor` argument threads the
calling agent identity through the invoker so destructive-op guardrails can
enforce attribution; handlers that don't care about identity accept it and
ignore it.

**Envelope Contract.** Every handler returns a JSON string. Success envelope
(data is always present, pagination appears only on list/collection responses):

```json
{
  "status": "ok",
  "data": {"example": "payload"},
  "pagination": {"total": 100, "offset": 0, "limit": 50}
}
```

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

**`common.py`** -- response envelopes, pagination output, guardrails,
placeholder factories:

- `ok(data, *, pagination=None)` -- success envelope with optional
  `PaginationMeta` metadata (frozen Pydantic model, `allow_inf_nan=False`).
- `err(exc, *, domain_code=None)` -- error envelope; `message` always goes
  through `safe_error_description(exc)` (SEC-1) and `domain_code` falls back
  to `exc.domain_code` when present.
- `not_supported(tool_name, reason)` -- stable `status="error"` /
  `domain_code="not_supported"` envelope for tools whose service facade is
  not yet wired. Emits the `MCP_HANDLER_NOT_IMPLEMENTED` WARNING event so
  operators can alert on unwired tools. After META-MCP-2 every tool is
  wired, so this path only fires for tools registered after PR1 that have
  not yet been given a concrete handler.
- `service_fallback(tool_name, reason)` -- helper retained in `common.py`
  for future surgical use. Emits `MCP_HANDLER_SERVICE_FALLBACK`;
  META-MCP-2 removed every call site and the integration sweep at
  `tests/integration/mcp/test_tool_surface.py` asserts zero emissions of
  this event across the full 204-tool surface.
- `capability_gap(tool_name, reason)` -- live handler whose underlying
  primitive does not yet expose the required method (e.g. agent
  `activity_feed`, memory fine-tune orchestrator on a backend that
  lacks fine-tune support). Identical wire envelope to `not_supported`
  (`domain_code="not_supported"`) but emits the dedicated
  `MCP_HANDLER_CAPABILITY_GAP` INFO event so ops telemetry distinguishes
  "primitive missing method" from "handler unwired".
- `require_destructive_guardrails(arguments, actor)` -- single source of
  truth for the destructive-op precondition triple: non-`None` `actor`,
  literal `confirm=True`, non-blank `reason`. Raises
  `GuardrailViolationError` with a typed `violation` code
  (`"missing_actor"` / `"missing_confirm"` / `"missing_reason"`).
- `paginate_sequence(seq, *, offset, limit, total=None)` -- in-memory
  page slicing that returns `(page, PaginationMeta)`.
- `dump_many(models)` -- batch Pydantic model serialisation to
  JSON-mode dicts.

**`common_args.py`** -- argument validators/extractors. Every helper
raises `ArgumentValidationError` on bad input so handlers can convert
to a stable `err(...)` envelope without catching framework-specific
exceptions:

- `require_arg(arguments, key, ty)` -- typed required-argument extraction
  (ruff `EM101`-safe).
- `require_non_blank(arguments, key)` -- required non-blank string,
  whitespace-stripped.
- `get_optional_str(arguments, key)` -- optional non-blank string;
  returns `None` when missing/empty.
- `require_dict(arguments, key, *, value_type=None, deep_copy=True)` --
  required dict argument; pass `value_type=str` for `dict[str, str]`
  validation. Defaults to deep-copying the input to decouple handler
  mutations from caller payload.
- `parse_time_window(arguments, *, until_required=True)` -- ISO 8601
  since/until parsing with timezone-aware enforcement and
  `since < until` ordering.
- `parse_str_sequence(arguments, key)` -- optional sequence-of-non-blank-strings.
- `coerce_pagination(arguments, *, default_limit=50)` -- offset/limit
  parsing with strict bounds and explicit bool rejection.
- `actor_id(actor)` / `require_actor_id(actor)` / `actor_label(actor)`
  -- actor identity helpers. Use `actor_id` for optional attribution,
  `require_actor_id` when attribution is mandatory (raises if
  unidentifiable), `actor_label` only for emit-only paths where a
  `"mcp-anonymous"` fallback is acceptable.

**`common_logging.py`** -- the three handler-layer log helpers.
Module-scoped logger keyed at `synthorg.meta.mcp.handlers` so test
assertions see a single stable event source regardless of which domain
handler emitted the event:

- `log_handler_argument_invalid(tool, exc)` -- caught
  `ArgumentValidationError`. Emits `MCP_HANDLER_ARGUMENT_INVALID` at
  WARNING.
- `log_handler_invoke_failed(tool, exc, **context)` -- generic
  `Exception` from the service layer. `**context` carries optional
  correlation ids (e.g. `task_id=`, `decision_id=`); keys that would
  shadow the canonical event fields (`tool_name`, `error_type`,
  `error`, `event`, `log_level`) are rejected with `ValueError`.
- `log_handler_guardrail_violated(tool, exc)` -- caught
  `GuardrailViolationError` from a destructive-op precondition.
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
`MappingProxyType` to enforce read-only access;
`build_handler_map()` in `src/synthorg/meta/mcp/handlers/__init__.py`
merges them and raises on duplicate keys.

**Schema-Level Validation.** Destructive-op schemas in
`src/synthorg/meta/mcp/domains/*.py` enforce the `reason` field as a
non-whitespace string via `"minLength": 1` + `"pattern": r".*\S.*"`, and the
`confirm` field as literal `True` via JSON Schema `"enum": [True]`. Handler
guardrails run regardless so validation stays uniform once services come
online.

## Progressive Tool Disclosure

When the tool inventory exceeds ~30 tools, loading every full definition into the LLM
context upfront becomes a major token tax. Progressive disclosure uses a three-level
hierarchy inspired by Google ADK's skill loading pattern:

| Level | Contents | When injected | Token cost |
|-------|----------|---------------|------------|
| **L1 metadata** | name, one-line description, category, cost tier | Always (system prompt) | ~100 tokens/tool |
| **L2 body** | full description, JSON Schema, examples, failure modes | On demand via `load_tool()` | <5K tokens/tool |
| **L3 resource** | markdown guides, code samples, example traces | Explicit via `load_tool_resource()` | Varies |

**Discovery tools** (always available regardless of agent access level):

- `list_tools()` -- returns L1 metadata for all permitted tools
- `load_tool(tool_name)` -- returns L2 body; marks tool as loaded in `AgentContext`
- `load_tool_resource(tool_name, resource_id)` -- returns specific L3 resource

**Context injection:**

1. L1 metadata is injected into the system prompt for all permitted tools
2. Full `ToolDefinition` objects are sent via the provider API `tools` parameter
   only for loaded tools + discovery tools
3. L3 resources are never auto-injected; returned inline from `load_tool_resource`

**Auto-unload:** When `AgentContext.context_fill_percent` exceeds
`ToolDisclosureConfig.unload_threshold_percent` (default 80%), the oldest-loaded
L2 body is unloaded (FIFO by insertion order). L1 metadata remains.

**Configuration** (`ToolDisclosureConfig`):

- `l1_token_budget` (default 3000) -- max tokens for L1 metadata
- `l2_token_budget` (default 15000) -- max tokens for loaded L2 bodies
- `auto_unload_on_budget_pressure` (default `true`)
- `unload_threshold_percent` (default 80.0)

Cross-reference: MCP integration above is the external tool integration pattern;
progressive disclosure is the local analogue for managing context cost.

## Action Type System

Action types classify agent actions for use by autonomy presets (see [Security & Approval](security.md#autonomy-levels)),
SecOps validation, tiered timeout policies, and progressive trust
([Decision Log](../architecture/decisions.md) D1).

**Registry:** `StrEnum` for ~26 built-in action types (type safety, autocomplete, typos caught
by static type checking and config-load-time validation) + `ActionTypeRegistry` for custom
types via explicit registration. Unknown strings are rejected at config load time -- a typo
in `human_approval` list silently meaning "skip approval" is a critical safety concern.

**Granularity:** Two-level `category:action` hierarchy. Category shortcuts expand to all
actions in that category (e.g., `auto_approve: ["code"]` expands to all `code:*` actions).
Fine-grained overrides are supported (e.g., `human_approval: ["code:create"]`).

**Taxonomy (~26 leaf types):**

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
memory:read
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
          description: "Full access for senior/trusted agents."
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
code execution isolation, and approval requirements against each tool invocation.  Per-agent
overrides can customize all six dimensions via `ToolPermissions.sub_constraints`.  K8s sandbox
backend integration is on the roadmap.

## Progressive Trust

Agents can earn higher tool access over time through configurable trust strategies. The trust
system implements a `TrustStrategy` protocol, making it extensible. All four strategies are
implemented.

!!! warning "Security Invariant"

    The `standard_to_elevated` promotion **always** requires human approval. No agent can
    auto-gain production access regardless of trust strategy.

=== "Disabled (Default)"

    Trust is disabled. Agents receive their configured access level at hire time and it never
    changes. Simplest option -- useful when the human manages permissions manually.

    ```yaml
    trust:
      strategy: "disabled"               # disabled, weighted, per_category, milestone
      initial_level: "standard"          # fixed access level for all agents
    ```

=== "Weighted Score"

    A single trust score computed from weighted factors: task difficulty completed, error rate,
    time active, and human feedback. One global trust level per agent, applied to all tool
    categories.

    ```yaml
    trust:
      strategy: "weighted"
      initial_level: "sandboxed"
      weights:
        task_difficulty: 0.3             # harder tasks completed = more trust
        completion_rate: 0.25
        error_rate: 0.25                 # inverse -- fewer errors = more trust
        human_feedback: 0.2
      promotion_thresholds:
        sandboxed_to_restricted: 0.4
        restricted_to_standard: 0.6
        standard_to_elevated:
          score: 0.8
          requires_human_approval: true  # always human-gated
    ```

    Simple model, easy to understand. One number to track. However, too coarse -- an agent
    trusted for file edits should not auto-gain deployment access.

=== "Per-Category"

    Separate trust tracks per tool category (filesystem, git, deployment, database, network).
    An agent can be "standard" for files but "sandboxed" for deployment. Promotion criteria
    differ per category.

    ```yaml
    trust:
      strategy: "per_category"
      initial_levels:
        file_system: "restricted"
        git: "restricted"
        code_execution: "sandboxed"
        deployment: "sandboxed"
        database: "sandboxed"
        terminal: "sandboxed"
      promotion_criteria:
        file_system:
          restricted_to_standard:
            tasks_completed: 10
            quality_score_min: 7.0
        deployment:
          sandboxed_to_restricted:
            tasks_completed: 20
            quality_score_min: 8.5
            requires_human_approval: true  # always human-gated for deployment
    ```

    Granular. Matches real security models (IAM roles). Prevents gaming via easy tasks. Trust
    state is a matrix per agent, not a scalar.

=== "Milestone Gates"

    Explicit capability milestones aligned with the Cloud Security Alliance Agentic Trust
    Framework. Automated promotion for low-risk levels. Human approval gates for elevated
    access. Trust is time-bound and subject to periodic re-verification.

    ```yaml
    trust:
      strategy: "milestone"
      initial_level: "sandboxed"
      milestones:
        sandboxed_to_restricted:
          tasks_completed: 5
          quality_score_min: 7.0
          auto_promote: true             # no human needed
        restricted_to_standard:
          tasks_completed: 20
          quality_score_min: 8.0
          time_active_days: 7
          auto_promote: true
        standard_to_elevated:
          requires_human_approval: true  # always human-gated
          clean_history_days: 14         # no errors in last 14 days
      re_verification:
        enabled: true
        interval_days: 90                # re-verify every 90 days
        decay_on_idle_days: 30           # demote one level if idle 30+ days
        decay_on_error_rate: 0.15        # demote if error rate exceeds 15%
    ```

    Industry-aligned. Re-verification prevents stale trust. Trust decay may need tuning
    to avoid frustrating users.

---

## See Also

- [Providers](providers.md) -- LLM abstraction and routing
- [Security & Approval](security.md) -- autonomy tiers, approval gates, progressive trust
- [Design Overview](index.md) -- full index
