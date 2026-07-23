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

```
ambient(all non-admin tools)  UNION  granted(agent's own mcp_capabilities)  UNION  operator_broadening(elevated_capabilities)
```

- `ambient` is always visible (the out-of-the-box surface).
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

## Supply-chain hardening: npm version pinning

The MCP catalog installer pins every npm package to `@<version>`, but a
hand-authored `MCPServerConfig` (in a config file or built
programmatically) bypasses that path. An `npx`-launched stdio server with
an unpinned (or `@latest`) package resolves whatever is newest on every
reconnect, so an un-reviewed version could start running under an agent's
tools with no config change.

`MCPServerConfig._validate_npm_pin` rejects an unpinned npm package at the
model boundary: an `npx` / `pnpm dlx` / `bunx` command must run a package
spec ending in a concrete `@<version>` (a floating tag such as `latest` /
`next` / `canary` is not a pin). Non-npx commands (node, python, docker)
are exempt. The stdio sandbox's `NPM_CONFIG_IGNORE_SCRIPTS=true` blocks
the install-script RCE vector independently, but does not stop an unpinned
package resolving a newer version, so the pin is a distinct control.

`check_mcp_server_config_pinned.py` guards the validator against removal.

## Residual scope (tracked)

- **Grant on demand.** The design intent is that a sensitive tool an
  agent is not yet granted can be *requested* at first use, approved once,
  and then persisted onto the agent's `mcp_capabilities` so future use is
  seamless. The visibility layer above ships first; the request-then-
  persist flow layers on top of the existing approval gate.
- **Rate limiting and destructive-call auto-escalation** on the external
  MCP bridge reuse the existing security rules engine + LLM safety
  classifier rather than a hand-authored per-server list.
