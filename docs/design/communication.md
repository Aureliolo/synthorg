---
title: Communication
description: Message bus architecture, communication patterns, standards, message format, and bus configuration / lifecycle.
---

# Communication

The communication architecture defines how agents exchange information. This
page covers transport-level concerns: patterns, standards, the message
contract, and bus configuration. Federation, orchestration, and the event
stream are split into sibling pages.

> **Strategic agents** (C-suite, VP, Director) receive additional anti-trendslop
> mitigation through the [Strategy module](strategy.md), which injects
> constitutional principles, multi-lens analysis, and confidence calibration
> into their system prompts.

## Related design docs

* [A2A External Gateway](communication-a2a.md): optional federation with external A2A-compatible systems (gateway, agent cards, concept mapping, SSE streaming, outbound client).
* [Communication Coordination](communication-coordination.md): loop prevention, conflict resolution strategies, meeting protocols, meeting scheduler, MCP service facades, and multi-agent failure pattern guardrails.
* [Event Stream and Async Delegation](communication-events.md): AG-UI projection, SSE endpoint, interrupt / resume protocol, EvidencePackage schema, async delegation steering tools, and citation tracking.

---

## Communication Patterns

The framework supports multiple communication patterns, configurable per company:

=== "Pattern 1: Event-Driven Message Bus"

    **Recommended Default**

    ```mermaid
    graph TD
        A[Agent A] --> Bus[Message Bus\nTopics/Queues]
        B[Agent B] --> Bus
        Bus --> T1["#engineering\n#code-review"]
        Bus --> T2["#product\n#design"]
        Bus --> T3["#all-hands\n#incidents"]
    ```

    - Agents publish to topics, subscribe to relevant channels
    - Async by default, enables parallelism
    - Decoupled: agents do not need to know about each other
    - Natural audit trail of all communications

    Best for
    :   Most scenarios; scales well, production-ready pattern.

=== "Pattern 2: Hierarchical Delegation"

    ```mermaid
    graph LR
        CEO --> CTO --> EL[Eng Lead]
        EL --> SD[Sr Dev] --> JD[Jr Dev]
        EL --> QAL[QA Lead] --> QAE[QA Eng]
    ```

    - Tasks flow down the hierarchy, results flow up
    - Each level can decompose and refine tasks before delegating
    - Authority enforcement built into the flow

    Best for
    :   Structured organisations with clear chains of command.

=== "Pattern 3: Meeting-Based"

    ```mermaid
    graph TD
        SP["Sprint Planning\nPM + CTO + Devs + QA + Design\nOutput: Sprint backlog"]
        DS["Daily Standup\nDevs + QA\nOutput: Status"]
        SP --> DS
    ```

    - Structured multi-agent conversations at defined intervals
    - Standup, sprint planning, retrospective, design review, code review

    Best for
    :   Agile workflows, decision-making, alignment.

=== "Pattern 4: Hybrid"

    Combines all three patterns:

    - **Message bus** for async daily work and notifications
    - **Hierarchical delegation** for task assignment and approvals
    - **Meetings** for cross-team decisions and planning ceremonies

Built-in templates select the communication pattern that fits their archetype (e.g.
`event_driven` for Solo Builder, Research Lab, and Data Team, `hierarchical` for Agency,
Enterprise Org, and Consultancy, `meeting_based` for Product Studio). See the
[Company Types table](organization.md#company-types) for per-template defaults.

---

## Communication Standards

The framework aligns with emerging industry standards:

A2A Protocol (Agent-to-Agent, Linux Foundation)
:   Inter-agent task delegation, capability discovery via Agent Cards, and
    structured task lifecycle management. See [A2A External Gateway](communication-a2a.md).

MCP (Model Context Protocol, Agentic AI Foundation / Linux Foundation)
:   Agent-to-tool integration, providing standardised tool discovery and
    invocation.

---

## Message Format

Messages use a **parts-based content model** with typed content parts
(`TextPart`, `DataPart`, `FilePart`, `UriPart`). The single `parts` tuple
carries all message content.

```json
{
  "id": "msg-uuid",
  "timestamp": "2026-02-27T10:30:00Z",
  "sender": "sarah_chen",
  "to": "engineering",
  "type": "task_update",
  "priority": "normal",
  "channel": "#backend",
  "parts": [
    {"type": "text", "text": "Completed API endpoint. PR ready for review."},
    {"type": "data", "data": {"pr_number": 42, "status": "open"}}
  ],
  "metadata": {
    "task_id": "task-123",
    "project_id": null,
    "tokens_used": 1200,
    "cost": 0.018,
    "extra": [["model", "example-medium-001"]]
  }
}
```

### Part Types

| Type | Discriminator | Key Fields | Purpose |
|------|---------------|-----------|---------|
| `TextPart` | `"text"` | `text: str` | Plain text content |
| `DataPart` | `"data"` | `data: dict` | Structured JSON payload (deep-frozen at construction) |
| `FilePart` | `"file"` | `uri: str`, `mime_type: str \| None` | File reference |
| `UriPart` | `"uri"` | `uri: str` | Generic URI reference |

`Message.text` is a computed field returning the first `TextPart.text`
(or `""` if no text parts exist) so callers reading message text by
attribute do not have to inspect the parts tuple.

All metadata fields are nullable except `extra`, which is always present (defaults to an empty list). The `extra` field contains additional key-value pairs for extensibility.

---

## Communication Config

???+ example "Full communication configuration"

    ```yaml
    communication:
      default_pattern: "hybrid"
      message_bus:
        backend: "internal"        # implemented: internal, nats
        channels:
          - "#all-hands"
          - "#engineering"
          - "#product"
          - "#design"
          - "#incidents"
          - "#code-review"
          - "#watercooler"
      meetings:
        enabled: true
        types:
          - name: "daily_standup"
            frequency: "per_sprint_day"
            participants: ["engineering", "qa"]
            duration_tokens: 2000
          - name: "sprint_planning"
            frequency: "bi_weekly"
            participants: ["all"]
            duration_tokens: 5000
          - name: "code_review"
            trigger: "on_pr"
            participants: ["author", "reviewers"]
      hierarchy:
        enforce_chain_of_command: true
        allow_skip_level: false    # can a junior message the CEO directly?
    ```

!!! info "Distributed bus backends"
    The `backend` field switches between the in-process `internal` default and the opt-in NATS JetStream backend for multi-process / multi-host deployments. See the [Distributed Runtime design](distributed-runtime.md) for the transport evaluation, stream layout, and migration path.

### Retention and Subscriber Bounds

Both bus backends are bounded on two independent axes so a slow subscriber cannot nuke the channel or leak memory without bound:

| Setting | Scope | In-memory backend | NATS backend |
|---------|-------|-------------------|--------------|
| `retention.max_messages_per_channel` | Per-channel history | `collections.deque(maxlen=...)` per channel | JetStream stream `max_msgs_per_subject` |
| `retention.max_subscriber_queue_size` | Per-(channel, subscriber) in-flight delivery | `asyncio.Queue(maxsize=...)` + drop-newest policy | `ConsumerConfig.max_ack_pending` (JetStream pauses delivery) |

When a slow subscriber's delivery cap is reached:

Internal backend
:   The publisher's `put_nowait` trips `asyncio.QueueFull`; the framework **drops the incoming envelope** (drop-newest), emits `COMM_SUBSCRIBER_QUEUE_OVERFLOW` at `WARNING` with `channel`, `subscriber`, `queue_size`, `drop_policy=newest`, `backend=memory`. Older queued envelopes are preserved so causality is maintained, and publishers never block.

NATS backend
:   JetStream stops handing new messages to the pull consumer once `max_ack_pending` is reached. Messages remain in the stream (subject to `max_messages_per_channel`), so when the consumer catches up delivery resumes automatically. The same `COMM_SUBSCRIBER_QUEUE_OVERFLOW` event is emitted (rate-limited) with `backend=nats` so operators see a uniform signal across backends.

The default `max_subscriber_queue_size` is `1024`, capped at `65535`. Raise it for bursty channels where subscribers are known to catch up within the burst; lower it to surface slow consumers faster. The upper limit guards against a misconfiguration or untrusted-config-source DoS.

### Bus Bridge Lifecycle Synchronization

`MessageBusBridge.start()` and `stop()` (the Litestar `ChannelsPlugin` bridge in `api/bus_bridge.py`) run under a dedicated `_lifecycle_lock` so the `_running` check-and-set + the per-channel `subscribe` + poll-task-spawn loop are atomic against concurrent lifecycle calls. A racing `stop()` cannot cancel in-flight subscribes while `start()` is still wiring channels, and two concurrent `start()` calls cannot both pass the `_running=False` check. Partial-subscribe failures are tracked per channel and escalated to `ERROR` with the full list of failed channels so a silent "started with incomplete channel coverage" state is always visible to operators. The drain in `stop()` (`asyncio.gather` on all in-flight poll tasks after cancellation) is spawned as a separate task, shielded from the outer `wait_for` cancellation, and capped at `_STOP_DRAIN_TIMEOUT_SECONDS = 10.0`s. On deadline, the bridge sets `_stop_failed = True`, logs an `ERROR`, and re-raises `TimeoutError`; the shielded drain continues running in the background but no longer holds the lifecycle lock. **After a timed-out stop, `start()` refuses all restart attempts on the same instance** (`_stop_failed` is the unrestartable flag) because orphaned poller tasks remain on the event loop; the only recovery is to construct a fresh `MessageBusBridge`; callers must replace the instance rather than retry `start()` on the same object. See `CLAUDE.md` "Lifecycle synchronization" for the shared pattern.

!!! warning "Slow audit sinks"
    The drop-newest policy means an audit/security sink that falls behind will silently lose new events. Run audit sinks on dedicated channels with `max_subscriber_queue_size` sized for the worst-case burst, or monitor `COMM_SUBSCRIBER_QUEUE_OVERFLOW` at WARNING and alert on it. Security-critical events should never share a channel with normal traffic.
