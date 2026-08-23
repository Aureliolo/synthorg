---
title: Communication Coordination
description: Loop prevention, MCP service facades, and multi-agent failure pattern guardrails.
---

# Communication Coordination

How agents coordinate during multi-agent interactions: delegation loops are
blocked, and known multi-agent failure modes carry explicit guardrails.

See also: [Communication](communication.md) (transport), [A2A Gateway](communication-a2a.md) (federation), [Event Stream](communication-events.md) (SSE + HITL).

## Loop Prevention

Agent communication loops (A delegates to B who delegates back to A) are a
critical risk. The framework enforces multiple safeguards:

| Mechanism | Description | Default |
|-----------|-------------|---------|
| **Max delegation depth** | Hard limit on chain length (A->B->C->D stops at depth N) | 5 |
| **Message rate limit** | Max messages per agent pair within a time window | 10 per minute |
| **Identical request dedup** | Detects and rejects duplicate task delegations within a window | 60s window |
| **Circuit breaker** | If an agent pair exceeds the bounce threshold, block further messages until manual reset or cooldown; the cooldown grows by exponential backoff on repeated trips | 3 bounces, 5min initial cooldown (capped at 1hr) |
| **Task ancestry tracking** | Every delegated task carries its full delegation chain; agents cannot delegate back to any ancestor in the chain | Always on |

???+ example "Loop prevention configuration"

    ```yaml
    loop_prevention:
      max_delegation_depth: 5
      rate_limit:
        max_per_pair_per_minute: 10
        burst_allowance: 3
      dedup_window_seconds: 60
      circuit_breaker:
        bounce_threshold: 3
        cooldown_seconds: 300
    ```

    Ancestry tracking is always enabled and is not user-configurable.

When a loop is detected, the framework:

1. Blocks the looping message
2. Notifies the sending agent with the detected loop chain
3. Escalates to the sender's manager (or human if at top of hierarchy)
4. Logs the loop for analytics and process improvement

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

*Research findings from #690 and #1254. See also:
[`docs/research/multi-agent-failure-audit.md`](../research/multi-agent-failure-audit.md)
and [S1 Multi-Agent Architecture Decision](../research/s1-multi-agent-decision.md).*

Empirical data (CIO, 2026) shows swarm topologies fail at 68% vs. 36% for hierarchical
orchestration. SynthOrg's orchestrated approach is validated, but the same failure modes
emerge if agent boundaries are poorly managed. This section documents current guardrails
and known risks.

### Group Conversation Safety

Multi-party agent conversations are bounded by `TokenTracker`
(`communication/multi_agent/token_tracker.py`), which holds a hard per-round
token budget and refuses the next turn once it is spent. The caller contract
(`communication/multi_agent/protocol.py`) is explicit about a missing
dependency: an unconfigured caller raises rather than returning an empty
response, so a wiring gap surfaces instead of producing meaningless
participation.

### Delegation Guard

Five mechanisms protect against swarm drift (`communication/loop_prevention/guard.py`):

1. Ancestry check (cycle prevention)
2. Max delegation depth (default 5)
3. Content deduplication (60s window)
4. Per-pair rate limiting (10/min)
5. Circuit breaker (3 bounces, exponential backoff cooldown capped at `max_cooldown_seconds`)

Circuit breaker uses exponential backoff: `cooldown = base * 2^(trip_count - 1)`,
capped at `max_cooldown_seconds` (default 3600s). On cooldown expiry, the bounce count
resets but the trip count is preserved, so successive trips produce progressively longer
cooldowns (#1116). Circuit breaker state (trip count, bounce count) is persisted to SQLite
via `CircuitBreakerStateRepository` so guardrails survive restarts. Dedup window and rate
limiter remain in-memory (short-lived by design).

### Microservices Anti-Patterns: Assessment

| Pattern | SynthOrg Risk | Mitigation |
|---|---|---|
| Chatty interfaces | Low; detected via `MessageOverhead.is_quadratic` | Detection exists; no enforcement circuit breaker |
| Distributed monolith | None; async pull message bus, no synchronous coupling | |
| Ownership ambiguity | None; TaskEngine single-writer actor | |
| Cascading failure | Low; `fail_fast` bounds wave propagation | No upstream contamination detection |
