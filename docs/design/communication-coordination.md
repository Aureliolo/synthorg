---
title: Communication Coordination
description: Delegation depth/cycle guard, MCP service facades, and multi-agent failure pattern guardrails.
---

# Communication Coordination

How agents coordinate during multi-agent interactions: delegation loops are
blocked, and known multi-agent failure modes carry explicit guardrails.

See also: [Communication](communication.md) (transport), [A2A Gateway](communication-a2a.md) (federation), [Event Stream](communication-events.md) (SSE + HITL).

## Delegation Depth and Cycle Guard

Blocking sub-agent delegation (`engine/delegation/`) is bounded by one guard
rather than a persisted per-pair state machine. Before a supervisor's
`delegate_and_await` tool spawns a child agent, `InProcessSubAgentRunner`
(`engine/delegation/runner.py`) walks the task's parent-task chain and
refuses when either condition holds:

| Condition | Check | Default |
|-----------|-------|---------|
| **Chain depth** | Refuse at or past `engine.delegation_max_depth` | 5 |
| **Cycle** | The target agent already appears as an ancestor task's assignee (including self-delegation) | Always on |

Both checks run in one pass over the parent-task chain, bounded by
`max_depth + 1` iterations. There is no separate persisted state to expire
or rehydrate: the chain itself, walked from the task graph, is the state. A
refusal raises `SubAgentDelegationDepthExceededError`, surfaced to the
supervisor as an ordinary tool failure on its next turn.

Two further caps apply to the child run itself, both read live per
delegation: `engine.delegation_max_turns` bounds the child's own turn count,
and `engine.delegation_timeout_seconds` bounds its wall-clock run (`0`
means no limit). `engine.delegation_enabled` is the feature's kill switch.

???+ example "Delegation depth configuration"

    ```yaml
    engine:
      delegation_enabled: true
      delegation_max_depth: 5
      delegation_max_turns: 10
      delegation_timeout_seconds: 0.0
    ```

This mechanism is unrelated to `coordination.max_delegation_rounds`, which
bounds the coordinator's own re-planning rounds rather than sub-agent
delegation depth.

---

## MCP Service Facades

The communication domain exposes four service facades on `AppState` for
MCP handler shims. Each is a thin wrapper; audit logging lives in the
facade rather than in the handler or the repository.

| Facade | Module | Tools shimmed |
|---|---|---|
| `MessageService` | `synthorg.communication.messages.service` | `synthorg_messages_list`/`_get`/`_send`/`_delete` |
| `ConnectionService` | `synthorg.integrations.connections.mcp_service` | `synthorg_connections_list`/`_get`/`_create`/`_delete`/`_check_health` |
| `WebhookService` | `synthorg.integrations.webhooks.service` | `synthorg_webhooks_list`/`_get`/`_create`/`_update`/`_delete` |
| `TunnelService` | `synthorg.integrations.tunnel.mcp_service` | `synthorg_tunnel_get_status`/`_connect` |

See `docs/design/tools.md` "SynthOrg MCP Tool Surface" for the handler
envelope contract. Deep-schema writes (create / update) use the Pydantic
pass-through pattern: the MCP tool's `inputSchema` is generated from the
same model the REST controller uses, so the wire contracts cannot drift.

---

## Multi-Agent Failure Pattern Guardrails

See also: [Multi-Agent Failure Audit](../research/multi-agent-failure-audit.md) and
[S1 Multi-Agent Architecture Decision](../research/s1-multi-agent-decision.md).

External research on multi-agent failure modes reports swarm topologies (agents
coordinating with no fixed structure) failing markedly more often than orchestrated
ones with defined agent boundaries. The guardrails below exist because the same
drift can appear inside an orchestrated system too when those boundaries are not
maintained: they reduce the drift rather than removing it. This section documents
the guardrails and the known risks.

### Group Conversation Safety

Multi-party agent conversations are bounded by `TokenTracker`
(`communication/multi_agent/token_tracker.py`), which holds a hard per-round
token budget and refuses the next turn once it is spent. The caller contract
(`communication/multi_agent/protocol.py`) is explicit about a missing
dependency: an unconfigured caller raises rather than returning an empty
response, so a wiring gap surfaces instead of producing meaningless
participation.

### Delegation Guard

Swarm drift via sub-agent delegation is bounded by the ancestry + depth guard
described above (`engine/delegation/runner.py`): a chain deeper than
`engine.delegation_max_depth`, or a target that already appears as an
ancestor's assignee, is refused before the child agent is spawned.

### Microservices Anti-Patterns: Assessment

| Pattern | SynthOrg Risk | Mitigation |
|---|---|---|
| Chatty interfaces | Low; detected via `MessageOverhead.is_quadratic` | Detection exists; no enforcement circuit breaker |
| Distributed monolith | None; async pull message bus, no synchronous coupling | |
| Ownership ambiguity | None; TaskEngine single-writer actor | |
| Cascading failure | Low; `fail_fast` bounds wave propagation | No upstream contamination detection |
