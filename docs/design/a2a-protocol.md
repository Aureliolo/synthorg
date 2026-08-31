---
title: A2A Protocol
description: Agent-to-Agent protocol integration. Status, architecture, implemented capabilities, Agent Card projection, and federation with external agent systems.
---

# A2A Protocol

The [A2A (Agent-to-Agent) protocol](https://a2a-protocol.org/) is a standard for heterogeneous agent communication. SynthOrg exposes an A2A gateway that lets external agent systems discover, invoke, and receive updates from the internal roster, without either side needing to understand the other's internal shape.

This page is the status-and-architecture reference: what ships today, how it maps onto SynthOrg's internal model, and what's next.

---

## Status

| Capability | Status |
|------------|--------|
| A2A gateway (`src/synthorg/api/a2a/gateway.py`) | Shipped |
| Agent Card serving (`GET /.well-known/agent-card.json`) | Shipped |
| JSON-RPC task submission + SSE streaming | Shipped |
| Agent Card projection from internal `AgentIdentity` | Shipped |
| Push notification subscription + webhook delivery | Shipped |
| Auth schemes: `apiKey`, `oauth2`, `bearer`, `mTLS`, `none` | Shipped |
| Allowlist-based inbound authorization | Shipped |
| Optional JWS Agent Card signature verification | Shipped |
| Webhook HMAC signature verification + replay protection | Shipped |
| SSRF validation on outbound webhooks | Shipped |
| DNS-rebind-hardened outbound SSRF (validated IP pinned via `PinnedDnsTransport`) | Shipped |
| Delegation depth/cycle guard on inbound requests | Not wired (see Loop Prevention below) |
| Quadratic communication enforcement strategies | Shipped (all four modes: `alert_only` default, `soft_throttle`, `hard_block`, `disabled`) |
| Inbound `skills/query` + `skills/negotiate` JSON-RPC handlers | Shipped |
| Outbound `query_skills` + `negotiate_skills` client methods (`A2AClient`) | Shipped |
| Governed peer discovery (`PeerDiscoveryClient`: SSRF-pinned card fetch + registry) | Shipped |
| Inter-org federation patterns (delegation across organisations) | Planned |

A2A is **disabled by default**. Enable via `a2a.enabled: true` in company YAML and configure auth + allowlist per deployment.

## Architecture

```d2
direction: right

ExtAgent: External agent

SynthOrg: {
  Gateway: A2A Gateway
  InternalBus: Internal MessageBus
  Hub: EventStreamHub
  Projection: project_event
  WebhookRX: A2APushVerifier

  Gateway -> InternalBus: "auth + allowlist + signature"
  Hub -> Projection
  WebhookRX -> InternalBus: "HMAC verify + replay dedup"
}

ExtAgent -> SynthOrg.Gateway: "JSON-RPC / SSE"
SynthOrg.Projection -> ExtAgent: "SSE or webhook"
ExtAgent -> SynthOrg.WebhookRX: "Push notification"
```

The gateway is a thin translation layer: inbound A2A requests become internal `MessageBus` messages after passing A2A-specific security checks. Outbound state is served through a per-consumer projection over the shared `EventStreamHub`, with no duplicate event source.

See [Security & Approval -> A2A Security](security.md#a2a-security) for the full auth, trust, webhook, and SSRF enforcement reference.

## Agent Card Projection

SynthOrg projects its internal `AgentIdentity` model to the A2A Agent Card format at `GET /.well-known/agent-card.json`. Every structured skill on an agent (`SkillSet.primary` + `SkillSet.secondary`) maps to an A2A `AgentSkill`:

| SynthOrg field | A2A AgentSkill | Purpose |
|----------------|----------------|---------|
| `Skill.id` | `id` | Unique skill identifier |
| `Skill.name` | `name` | Human-readable display name |
| `Skill.description` | `description` | Capability description for semantic matching |
| `Skill.tags` | `tags` | Searchable tags for multi-faceted routing |
| `Skill.input_modes` | `inputModes` | MIME types accepted |
| `Skill.output_modes` | `outputModes` | MIME types produced |
| `Skill.proficiency` | - | SynthOrg-specific; not projected (no A2A field yet) |

See [Agents -> Skill Model](agents.md#skill-model) for the skill structure.

## Loop Prevention

The internal [ancestry + depth guard](communication-coordination.md#delegation-depth-and-cycle-guard)
(`engine.delegation_max_depth`, cycle check on the parent-task chain) is not wired to
the A2A gateway: `message/send` creates a new root task via `task_engine.create_task`,
which has no parent-task chain for the guard to walk. An external peer that is itself
being driven by a runaway internal delegation loop is bounded by that loop's own guard
on its own side, not by anything this gateway checks.

## Quadratic Communication Detection

`MessageOverhead.is_quadratic` flags configurations where pairwise agent-to-agent messaging approaches `O(n^2)`. External agent federation can amplify this (every external connection potentially talks to every internal agent).

Four enforcement strategies are defined behind `QuadraticEnforcementStrategy` and wired into the in-memory bus (`message_bus.quadratic_enforcement`). Detection compares a sliding-window inter-agent publish count against `team_size^2 * quadratic_threshold`; the strategy decides the response. Every mode emits a structured `communication.quadratic.detected` event (rate-limited to once per window) and forwards to a late-bound `QuadraticAlertSink` (the enforcer is decoupled from the notification subsystem via this protocol; boot wiring binds it to the `NotificationDispatcher` adapter) when one is wired.

| Strategy | Status | Behaviour |
|----------|--------|-----------|
| `alert_only` (default) | Shipped | Detect and emit warning event + `QuadraticAlertSink` notification |
| `soft_throttle` | Shipped | Alert, then apply publish backpressure (per-publish delay) to the over-communicating bus |
| `hard_block` | Shipped | Alert, then reject new agent connections once the live participant count reaches `max_agent_connections` |
| `disabled` | Shipped | No detection or enforcement (zero hot-path cost) |

See [Security -> Quadratic Communication Enforcement](security.md#quadratic-communication-enforcement) for the config surface.

## Configuration Summary

The full A2A config is documented in [Security -> A2AConfig](security.md#a2aconfig). The minimum viable production setup:

```yaml
a2a:
  enabled: true
  auth:
    inbound: apiKey
    outbound: bearer
    api_key: "${A2A_API_KEY}"
    outbound_token: "${A2A_OUTBOUND_TOKEN}"
  allowed_agents:
    - "https://partner.example.com/.well-known/agent-card.json"
  max_request_body_bytes: 1048576
```

`none` inbound auth is rejected for production deployments. Agent Card signature verification (`agent_card_verification.require_signatures: true`) is recommended when peers are untrusted.

---

## See Also

- [Security & Approval](security.md#a2a-security): authentication, trust, webhook, SSRF details
- [Communication A2A Gateway](communication-a2a.md): gateway architecture, agent card projection, SSE streaming
- [Agents](agents.md#skill-model): internal skill shape that gets projected to A2A Agent Card
- [Reference: Standards](../reference/standards.md): protocol compliance table
