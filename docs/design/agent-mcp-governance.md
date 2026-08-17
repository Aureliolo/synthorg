# Agent MCP Governance

A running agent can call SynthOrg's own MCP tools through its ordinary
tool invoker (the self-consumer bridge) and can reach external MCP servers
through the MCP bridge tool. This page covers how that surface is scoped
per agent and hardened against supply-chain and blast-radius risk.

## Per-agent MCP visibility (progressive capabilities)

`ToolAccessLevel` is an agent's earned trust tier
(`SANDBOXED < RESTRICTED < STANDARD < ELEVATED`). ELEVATED is the top:
the surface an agent can drive without a per-call approval. The problem
this design closes: previously *every* ELEVATED agent saw the whole
~247-tool MCP surface (fire staff, deploy, delete, mutate the org), so a
prompt-injection hijacking any ELEVATED agent could reach the highest
blast-radius tools.

### Ambient versus sensitive

Each MCP tool carries a `domain:action` capability tag. The action marks
its tier:

- **Ambient** (`domain:read`, `domain:write`): usable out of the box by
  any ELEVATED agent, with zero per-agent configuration. Reading and
  ordinary writing must work immediately or the surface is unusable.
- **Sensitive** (`domain:admin`): the high-blast-radius tools that carry
  the `require_admin_guardrails` confirm+reason gate. Visible to an
  ELEVATED agent only when explicitly granted.

### The bridge

`engine/mcp_self_consumer.py::_provide` computes an ELEVATED agent's
visible tools as:

```text
ambient(all non-admin tools)  UNION  granted(agent's own mcp_capabilities)  UNION  operator_broadening(elevated_capabilities)
```

- `ambient` is always visible (the default surface).
- `granted` is the agent's own `ToolPermissions.mcp_capabilities` (e.g.
  `("agents:admin",)`): the sensitive families it earned. Empty by
  default, so an unconfigured agent gets exactly the ambient surface.
- `elevated_capabilities` (default `()`) is an operator-set org-wide
  broadening; setting it to `("*",)` restores the whole surface.
- `denied_tools` is the highest-priority denylist, applied last.

Sub-ELEVATED agents keep the explicit operator `read_tool_allowlist` path
(empty by default: no MCP for low-trust agents).

Because sensitive tools are hidden until granted, a prompt-injected
marketing agent cannot see or call `deploy` / `org_fire` / `delete`
tools: the surface is not there to attack. And because ambient tools are
always visible, the surface works with no per-agent setup.

`check_mcp_self_consumer_scoped.py` guards that the bridge keeps reading
`identity.tools.mcp_capabilities`; if it regressed to a single global
grant, every ELEVATED agent would again see everything.

## Where a stdio MCP server actually runs

A stdio MCP server is arbitrary third-party code, so it runs in a container,
never as a child of the backend. Getting that wrong is not theoretical: it is
what made the shipped catalog unlaunchable on every shipped stack.

The backend image is hardened and ships no shell, no node and no `npx`, so a
direct spawn raises `FileNotFoundError`. The wrapper that existed to solve
that rewrote the launch to `docker run -i ...`, and the image ships no
`docker` binary either, so it raised the same error from one line further
along. A live boot logged `mcp.client.credentials_injected` (the operator's
install was correct), then `connection_failed error='FileNotFoundError'`,
then `mcp.factory.complete tool_count=0`, and moved on. Install-time
validation checked credentials thoroughly and never asked whether this
process could launch the thing at all.

### The transport

`tools/mcp/container_stdio.py` reaches the daemon the way the rest of the
product does, over the API. It creates the container, attaches to its
`stdin` and `stdout` **before** starting it (so no output frame is lost and
the session's first request has somewhere to go), and yields the same
`(read, write)` memory-stream pair the SDK's `stdio_client` yields:
line-delimited JSON-RPC in both directions, a parse failure delivered as a
value rather than an exception, and `stderr` logged and never parsed.

Isolation is the same policy the CLI wrapper asked for, expressed as
`HostConfig`: every capability dropped, no new privileges, a read-only root
with one writable tmpfs, and the operator's memory / pids / `cpu` / network
limits (`tools.mcp_sandbox_*`, converted to daemon units by
`tools/sandbox/_container_limits.py`). The container keeps the image's own
`uid`, as the agent sandbox does, because naming a user here would bind the
transport to one image's accounts.

Three properties beyond the isolation are load-bearing:

- **Trusted controls win by construction.** `HOME`, `NPM_CONFIG_CACHE` and
  `NPM_CONFIG_IGNORE_SCRIPTS` are merged last, so a configured environment
  cannot re-enable install scripts (the npm RCE vector) or redirect writes
  off the one writable mount. A collision is logged, not silently dropped.
- **The container is attributable.** It carries the managed label and this
  deployment's label (both owned by `tools/sandbox/deployment_identity.py`,
  derived from the agent workspace root). Without them the boot
  reconciliation pass leaves an orphan alone for ever, and a hard kill of the
  backend leaves a credentialed server running with nothing attached to it.
- **A failure keeps its type.** A task group re-raises what escapes its body
  as an `ExceptionGroup`. The client's reconnect handler retries an
  `MCPConnectionError` and nothing else, so the transport carries a
  session-time failure out of the group and re-raises it unchanged.

### One image runs untrusted code

The runtime image is the resolved `tools.sandbox_image`: it carries Node, npm
and Python, and the CLI verifies its signature. `tools.mcp_sandbox_image` is
deleted. A second knob naming a second image is a second answer to a question
the operator already answered by hardening and verifying one image, and its
default named a third-party image the deployment had never pulled.

### Refusing what cannot be launched

`installation_to_server_config` is the single owner of "can this entry become
a runnable server". `CatalogService.install` calls it before persisting a row,
so an install refuses exactly what a boot would refuse, at the one moment an
operator is present to be told; a boot skips a row it refuses rather than
failing, so one bad row does not cost an operator every other server.

`RUNTIME_PROGRAMS` in `tools/mcp/runtime_provision.py` declares each
launchable program together with the apko package that installs it.
`check_mcp_catalog_launchable.py` holds that declaration to
`docker/sandbox/apko.yaml` in both directions: a declared program no package
provides fails the build, and so does a bundled entry naming an undeclared
program. It fails closed on an empty declaration, because a gate looking at
nothing must not report success.

## Supply-chain hardening: npm version pinning

The MCP catalog installer pins every npm package to `@<version>`, but a
hand-authored `MCPServerConfig` (in a config file or built
programmatically) bypasses that path. An `npx`-launched stdio server with
an unpinned (or `@latest`) package resolves whatever is newest on every
reconnect, so an un-reviewed version could start running under an agent's
tools with no config change.

`MCPServerConfig._validate_npm_pin` rejects an unpinned npm package at the
model boundary: an `npx` / `pnpm dlx` / `bunx` command must run a package
spec ending in an exact `@<version>`. Only `MAJOR.MINOR.PATCH` (with
optional pre-release/build metadata) names one immutable artifact, so a
dist-tag (`latest` / `next` / `canary`), a range (`^1.2.3` / `~1.2.3` /
`>=1.2.3`), a partial version (`1` / `1.2`), and a wildcard (`1.x` / `*`)
are all refused: each still re-resolves at spawn time. `npx` reads its own
options only up to the first positional, so a `--package` after it is an
argument forwarded to the spawned binary, not a second install.
`CatalogEntry.npm_version` applies the same rule through the shared
`core.npm_version.is_exact_npm_version`, so the curated and hand-authored
paths cannot drift apart. Non-npx commands (node, python, docker) are
exempt. The stdio sandbox's `NPM_CONFIG_IGNORE_SCRIPTS=true` blocks the
install-script RCE vector independently, but does not stop an unpinned
package resolving a newer version, so the pin is a distinct control.

`check_mcp_server_config_pinned.py` guards the validator against removal.

## Catalog credential binding

A catalog entry's `credential_env_map` maps a bound connection's credential
field to the environment variable its MCP server reads. Injection is an
exact field-name lookup at connect time with no aliasing, so an entry naming
a field the required connection type never stores injects nothing: the
server launches unauthenticated, the only signal is a warning nobody is
watching for, and the failure resurfaces much later as an opaque upstream
auth error.

Two checks close that gap from both sides. `CatalogService.install` refuses
a bound connection missing any mapped field, naming the field, rather than
recording an installation that can only fail. `check_catalog_credential_fields.py`
compares every bundled entry against the field registry, because the entry
and the fields live in different files and nothing else notices them drift.

## Destructive external-MCP auto-escalation

Every MCP call already flows through the same `ToolInvoker` security
interceptor as a native tool (there is no MCP bypass), but the built-in
`DestructiveOpDetector` only recognises shell/SQL command *syntax*
(`rm -rf`, `DROP TABLE`) embedded in string arguments. A third-party MCP
call never carries that: its intent lives in the tool name
(`mcp_github_delete_repository`) or a structured dispatch argument
(`{"action": "delete_channel"}`), so a destructive third-party operation
sailed through as a plain `comms:external` `ALLOW`.

`MCPDestructiveOpDetector` (a `SecurityRule`, registered right after the
shell detector, gated by `RuleEngineConfig.mcp_destructive_op_detection_enabled`)
closes that gap. It fires only for `ToolCategory.MCP`, tokenises the tool
name and string argument values, and **escalates** any call whose operation
reads as destructive (delete / purge / revoke / terminate ...): HIGH, or
CRITICAL for mass-destruction verbs. It only ever escalates, never
auto-denies: a human, not a regex, makes the final call on a third-party
operation, and escalation is the safe direction (an over-broad match costs
a confirmation, never data). A rule-matched verdict is authoritative and
bypasses the LLM fallback, which stays reserved for the low-confidence
minority of unclassified MCP calls.

A HIGH/CRITICAL `ESCALATE` verdict routes to the approval gate through the
existing `_handle_escalation` -> `pending_escalations` -> `should_park`
chain. That chain had a latent no-op: `ToolInvoker._check_security` skipped
the escalation when a verdict reached it with no `approval_id` (an
interceptor that never actually parked the call), yet still returned an
"approval required" result, so the destructive call was blocked but the
escalation was silently dropped instead of reaching a human. The invoker
now **fails closed** on that combination (a loud error log plus a blocked
result), so an unattributable escalation can never slip through without review.

## Residual scope (tracked)

- **Grant on demand.** The design intent is that a sensitive tool an
  agent is not yet granted can be *requested* at first use, approved once,
  and then persisted onto the agent's `mcp_capabilities` so future use is
  seamless. The visibility layer above ships first; the request-then-
  persist flow layers on top of the existing approval gate.
