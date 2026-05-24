---
title: A2A External Gateway
description: Optional external interface enabling SynthOrg agents to federate with agents in other A2A-compatible systems via JSON-RPC and SSE.
---

# A2A External Gateway

The A2A gateway is an **optional** external interface that enables SynthOrg agents to
federate with agents in other A2A-compatible systems. It is disabled by default
(`a2a.enabled: false`). Internal communication is unchanged; the MessageBus remains the
sole transport for intra-organisation messages.

See also: [Communication](communication.md) (transport), [Coordination](communication-coordination.md) (loop prevention referenced below), [Event Stream](communication-events.md) (SSE hub).

## Architecture

```d2
External: "External A2A Agent\n(other framework)"

External -> SynthOrg.Gateway: "JSON-RPC / SSE"

SynthOrg: "SynthOrg Organization" {
  Gateway: "A2A Gateway\n(optional, disabled by default)"

  Bus: "Message Bus\n(internal, unchanged)"

  Gateway -> Bus: inbound
  Bus -> Gateway: outbound

  Agents: "Internal Agents" {
    grid-columns: 4
    A1
    A2
    A3
    A4
  }

  Bus -> Agents
}
```

The gateway sits at the organisation boundary and handles two directions:

Inbound (external -> internal)
:   External A2A clients discover SynthOrg agents via Agent Cards, create tasks via
    JSON-RPC, and receive updates via SSE. The gateway translates A2A requests into
    internal MessageBus messages and applies [DelegationGuard](communication-coordination.md#loop-prevention) +
    [A2A-specific security checks](security.md#a2a-security) before admission.

Outbound (internal -> external)
:   SynthOrg agents can delegate tasks to external A2A agents. The A2A client discovers
    external agents via their Agent Card URLs, creates tasks, and maps external task
    states back to internal states.

## Agent Card Projection

When the gateway is enabled, SynthOrg serves an Agent Card at
`/.well-known/agent.json` per the A2A specification. The card is a **safe projection**
of `AgentIdentity`; only fields relevant to external capability discovery are exposed:

| AgentIdentity Field | Agent Card Field | Included | Rationale |
|---------------------|-----------------|----------|-----------|
| `name` | `name` | Yes | Public identity |
| `role` | `description` (partial) | Yes | Capability context |
| `skills` (SkillSet) | `skills` (AgentSkill[]) | Yes | Lossless mapping via [Skill model](agents.md#skill-model) |
| `department` | metadata | Optional | Organisational context |
| `personality` | - | No | Internal behavioural tuning |
| `level` (seniority) | - | No | Internal authority hierarchy |
| `authority` | - | No | Internal delegation rules |
| `model` (ModelConfig) | - | No | Internal infrastructure |
| `tools` | - | No | Security-sensitive capability list |
| `budget_limit` | - | No | Internal financial data |

The [Skill model](agents.md#skill-model) is A2A AgentSkill-aligned on the shared
capability fields (`id`, `name`, `description`, `tags`, `input_modes`,
`output_modes`).  Those fields project losslessly in both directions. The
SynthOrg-only `proficiency` field has no A2A counterpart, so:

- **SynthOrg -> A2A**: `proficiency` is dropped from the projected `AgentSkill`.
- **A2A -> SynthOrg**: imported `AgentSkill` objects populate `proficiency`
  from the internal default (`1.0`) since the wire format does not carry it.

## Concept Mapping

SynthOrg and A2A use different terminology for overlapping concepts. This table provides
a bidirectional reference for the gateway translation layer.

### Task State Mapping

| SynthOrg State | A2A State | Direction | Notes |
|----------------|-----------|-----------|-------|
| `CREATED` | `submitted` | Bidirectional | Initial task creation |
| `ASSIGNED` | `working` | SynthOrg -> A2A | Agent has accepted the task |
| `IN_PROGRESS` | `working` | Bidirectional | Active execution |
| `IN_REVIEW` | `working` | SynthOrg -> A2A | Internal review stage, opaque externally |
| `BLOCKED` | `input-required` | Bidirectional | Waiting for external input |
| `SUSPENDED` | `input-required` | SynthOrg -> A2A | Approval-parked tasks |
| `INTERRUPTED` | `failed` | SynthOrg -> A2A | Interrupted execution; externally indistinguishable from failure |
| `COMPLETED` | `completed` | Bidirectional | Successful completion |
| `FAILED` | `failed` | Bidirectional | Unrecoverable failure |
| `CANCELLED` | `canceled` | Bidirectional | Client-initiated cancellation |
| `REJECTED` | `rejected` | Bidirectional | Task refused by agent or guard |

**Gate verdict mapping** (not a task state):

| SynthOrg Verdict | A2A State | Direction | Notes |
|------------------|-----------|-----------|-------|
| Approval gate `ESCALATED` | `auth-required` | SynthOrg -> A2A | Gateway maps ESCALATED verdict to A2A auth-required; external client must provide additional credentials |

### Identity Mapping

| SynthOrg | A2A | Direction | Notes |
|----------|-----|-----------|-------|
| `AgentIdentity` | `AgentCard` | SynthOrg -> A2A | One-way projection (safe subset) |
| `Skill` | `AgentSkill` | Bidirectional | Lossless on shared capability fields; internal-only `proficiency` is dropped outbound and defaulted to `1.0` on inbound |
| `SkillSet.primary` | `AgentCard.skills` (tagged `primary`) | SynthOrg -> A2A | Primary/secondary distinction preserved via tags |
| `SkillSet.secondary` | `AgentCard.skills` (tagged `secondary`) | SynthOrg -> A2A | Primary/secondary distinction preserved via tags |

### Message and Task Lifecycle Mapping

| SynthOrg | A2A | Notes |
|----------|-----|-------|
| `Message` (internal) | `Message` + `Part[]` (A2A) | Internal message content maps to A2A text parts |
| `DelegationRequest` | `tasks/send` (JSON-RPC) | Task creation |
| `DelegationResult` | `tasks/get` response | Task completion/status |
| `TaskExecution` state | Task object + `status` | Ongoing task tracking |
| MessageBus channels | - | No A2A equivalent; internal routing only |
| Meeting protocols | - | No A2A equivalent; internal coordination only |

## SSE Streaming

External task update delivery uses **Server-Sent Events** per the A2A specification.
The dashboard also uses SSE for observability and the HITL interrupt/resume protocol
(see [Event Stream and HITL Surface](communication-events.md#sse-endpoint)).

| Consumer | Transport | Protocol | Use Case |
|----------|-----------|----------|----------|
| Web dashboard | SSE | AG-UI projected events | Observability + HITL interrupt/resume |
| Web dashboard | WebSocket | Custom events | Bidirectional UI actions (chat, settings) |
| External A2A client | SSE | `tasks/sendSubscribe` | Task progress streaming |

The `EventStreamHub` is the single event source for all SSE consumers (hub-driven
architecture). Both the AG-UI dashboard and the A2A gateway subscribe
to the hub and apply per-consumer projection layers. The gateway applies an A2A
projection that filters to task-related events for explicitly subscribed tasks and
formats payloads per the A2A specification; no internal channel traffic leaks to
external consumers.

## A2A Client (Outbound)

SynthOrg agents can delegate tasks to external A2A agents through the outbound client:

1. **Discovery**: Fetch the external agent's Agent Card from its well-known URL
2. **Skill import**: Deserialise `AgentSkill[]` into internal `Skill` model (lossless)
3. **Task creation**: Send `tasks/send` JSON-RPC request with auth credentials
4. **Monitoring**: Subscribe to task updates via SSE or poll via `tasks/get`
5. **State mapping**: Map external A2A task states back to internal states (see table above)

The outbound client authenticates using the `a2a.auth.outbound` configuration (see
[A2A Security](security.md#a2a-security)). Outbound delegations pass through the
[DelegationGuard](communication-coordination.md#loop-prevention) for loop-prevention checks (ancestry, depth,
deduplication, rate limiting, circuit breaker) before dispatch.
