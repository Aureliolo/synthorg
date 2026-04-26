---
title: Communication
description: Message bus architecture, delegation, conflict resolution strategies, and meeting protocols for inter-agent communication.
---

# Communication

The communication architecture defines how agents exchange information, resolve
disagreements, and coordinate through structured meetings. All communication
patterns, conflict resolution strategies, and meeting protocols are pluggable
and configurable per company, per department, or per interaction type.

> **Strategic agents** (C-suite, VP, Director) receive additional anti-trendslop
> mitigation through the [Strategy module](strategy.md), which injects
> constitutional principles, multi-lens analysis, and confidence calibration
> into their system prompts.

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
    - Decoupled -- agents do not need to know about each other
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
    :   Structured organizations with clear chains of command.

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
    structured task lifecycle management.

MCP (Model Context Protocol, Agentic AI Foundation / Linux Foundation)
:   Agent-to-tool integration, providing standardized tool discovery and
    invocation.

---

## A2A External Gateway

The A2A gateway is an **optional** external interface that enables SynthOrg agents to
federate with agents in other A2A-compatible systems. It is disabled by default
(`a2a.enabled: false`). Internal communication is unchanged -- the MessageBus remains the
sole transport for intra-organization messages.

### Architecture

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

The gateway sits at the organization boundary and handles two directions:

Inbound (external -> internal)
:   External A2A clients discover SynthOrg agents via Agent Cards, create tasks via
    JSON-RPC, and receive updates via SSE. The gateway translates A2A requests into
    internal MessageBus messages and applies [DelegationGuard](#loop-prevention) +
    [A2A-specific security checks](security.md#a2a-security) before admission.

Outbound (internal -> external)
:   SynthOrg agents can delegate tasks to external A2A agents. The A2A client discovers
    external agents via their Agent Card URLs, creates tasks, and maps external task
    states back to internal states.

### Agent Card Projection

When the gateway is enabled, SynthOrg serves an Agent Card at
`/.well-known/agent.json` per the A2A specification. The card is a **safe projection**
of `AgentIdentity` -- only fields relevant to external capability discovery are exposed:

| AgentIdentity Field | Agent Card Field | Included | Rationale |
|---------------------|-----------------|----------|-----------|
| `name` | `name` | Yes | Public identity |
| `role` | `description` (partial) | Yes | Capability context |
| `skills` (SkillSet) | `skills` (AgentSkill[]) | Yes | Lossless mapping via [Skill model](agents.md#skill-model) |
| `department` | metadata | Optional | Organizational context |
| `personality` | -- | No | Internal behavioral tuning |
| `level` (seniority) | -- | No | Internal authority hierarchy |
| `authority` | -- | No | Internal delegation rules |
| `model` (ModelConfig) | -- | No | Internal infrastructure |
| `tools` | -- | No | Security-sensitive capability list |
| `budget_limit` | -- | No | Internal financial data |

The [Skill model](agents.md#skill-model) is A2A AgentSkill-aligned on the shared
capability fields (`id`, `name`, `description`, `tags`, `input_modes`,
`output_modes`).  Those fields project losslessly in both directions.  The
SynthOrg-only `proficiency` field has no A2A counterpart, so:

- **SynthOrg -> A2A**: `proficiency` is dropped from the projected `AgentSkill`.
- **A2A -> SynthOrg**: imported `AgentSkill` objects populate `proficiency`
  from the internal default (`1.0`) since the wire format does not carry it.

### Concept Mapping

SynthOrg and A2A use different terminology for overlapping concepts. This table provides
a bidirectional reference for the gateway translation layer.

#### Task State Mapping

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
| `REJECTED` *(proposed)* | `rejected` | Bidirectional | Task refused by agent or guard (requires new TaskStatus value) |

**Gate verdict mapping** (not a task state):

| SynthOrg Verdict | A2A State | Direction | Notes |
|------------------|-----------|-----------|-------|
| Approval gate `ESCALATED` | `auth-required` | SynthOrg -> A2A | Gateway maps ESCALATED verdict to A2A auth-required; external client must provide additional credentials |

#### Identity Mapping

| SynthOrg | A2A | Direction | Notes |
|----------|-----|-----------|-------|
| `AgentIdentity` | `AgentCard` | SynthOrg -> A2A | One-way projection (safe subset) |
| `Skill` | `AgentSkill` | Bidirectional | Lossless on shared capability fields; internal-only `proficiency` is dropped outbound and defaulted to `1.0` on inbound |
| `SkillSet.primary` | `AgentCard.skills` (tagged `primary`) | SynthOrg -> A2A | Primary/secondary distinction preserved via tags |
| `SkillSet.secondary` | `AgentCard.skills` (tagged `secondary`) | SynthOrg -> A2A | Primary/secondary distinction preserved via tags |

#### Message and Task Lifecycle Mapping

| SynthOrg | A2A | Notes |
|----------|-----|-------|
| `Message` (internal) | `Message` + `Part[]` (A2A) | Internal message content maps to A2A text parts |
| `DelegationRequest` | `tasks/send` (JSON-RPC) | Task creation |
| `DelegationResult` | `tasks/get` response | Task completion/status |
| `TaskExecution` state | Task object + `status` | Ongoing task tracking |
| MessageBus channels | -- | No A2A equivalent; internal routing only |
| Meeting protocols | -- | No A2A equivalent; internal coordination only |

### SSE Streaming

External task update delivery uses **Server-Sent Events** per the A2A specification.
The dashboard also uses SSE for observability and the HITL interrupt/resume protocol
(see [Event Stream & HITL Surface](#event-stream--hitl-surface) below).

| Consumer | Transport | Protocol | Use Case |
|----------|-----------|----------|----------|
| Web dashboard | SSE | AG-UI projected events | Observability + HITL interrupt/resume |
| Web dashboard | WebSocket | Custom events | Bidirectional UI actions (chat, settings) |
| External A2A client | SSE | `tasks/sendSubscribe` | Task progress streaming |

The `EventStreamHub` is the single event source for all SSE consumers (hub-driven
architecture). Both the AG-UI dashboard and the A2A gateway subscribe
to the hub and apply per-consumer projection layers. The gateway applies an A2A
projection that filters to task-related events for explicitly subscribed tasks and
formats payloads per the A2A specification -- no internal channel traffic leaks to
external consumers.

### A2A Client (Outbound)

SynthOrg agents can delegate tasks to external A2A agents through the outbound client:

1. **Discovery**: Fetch the external agent's Agent Card from its well-known URL
2. **Skill import**: Deserialize `AgentSkill[]` into internal `Skill` model (lossless)
3. **Task creation**: Send `tasks/send` JSON-RPC request with auth credentials
4. **Monitoring**: Subscribe to task updates via SSE or poll via `tasks/get`
5. **State mapping**: Map external A2A task states back to internal states (see table above)

The outbound client authenticates using the `a2a.auth.outbound` configuration (see
[A2A Security](security.md#a2a-security)). Outbound delegations pass through the
[DelegationGuard](#loop-prevention) for loop-prevention checks (ancestry, depth,
deduplication, rate limiting, circuit breaker) before dispatch.

---

## Message Format

Messages use a **parts-based content model** with typed content parts
(`TextPart`, `DataPart`, `FilePart`, `UriPart`).  The flat `content: str`
field and `attachments` array have been removed in favour of a single
`parts` tuple.

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
(or `""` if no text parts exist), providing backward-compatible text access.

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

`MessageBusBridge.start()` and `stop()` (the Litestar `ChannelsPlugin` bridge in `api/bus_bridge.py`) run under a dedicated `_lifecycle_lock` so the `_running` check-and-set + the per-channel `subscribe` + poll-task-spawn loop are atomic against concurrent lifecycle calls. A racing `stop()` cannot cancel in-flight subscribes while `start()` is still wiring channels, and two concurrent `start()` calls cannot both pass the `_running=False` check. Partial-subscribe failures are tracked per channel and escalated to `ERROR` with the full list of failed channels so a silent "started with incomplete channel coverage" state is always visible to operators. The drain in `stop()` (`asyncio.gather` on all in-flight poll tasks after cancellation) is spawned as a separate task, shielded from the outer `wait_for` cancellation, and capped at `_STOP_DRAIN_TIMEOUT_SECONDS = 10.0`s. On deadline, the bridge sets `_stop_failed = True`, logs an `ERROR`, and re-raises `TimeoutError`; the shielded drain continues running in the background but no longer holds the lifecycle lock. **After a timed-out stop, `start()` refuses all restart attempts on the same instance** (`_stop_failed` is the unrestartable flag) because orphaned poller tasks remain on the event loop; the only recovery is to construct a fresh `MessageBusBridge` -- callers must replace the instance rather than retry `start()` on the same object. See `CLAUDE.md` §"Lifecycle synchronization" for the shared pattern.

!!! warning "Slow audit sinks"
    The drop-newest policy means an audit/security sink that falls behind will silently lose new events. Run audit sinks on dedicated channels with `max_subscriber_queue_size` sized for the worst-case burst, or monitor `COMM_SUBSCRIBER_QUEUE_OVERFLOW` at WARNING and alert on it. Security-critical events should never share a channel with normal traffic.

---

## Loop Prevention

Agent communication loops (A delegates to B who delegates back to A) are a
critical risk. The framework enforces multiple safeguards:

| Mechanism | Description | Default |
|-----------|-------------|---------|
| **Max delegation depth** | Hard limit on chain length (A->B->C->D stops at depth N) | 5 |
| **Message rate limit** | Max messages per agent pair within a time window | 10 per minute |
| **Identical request dedup** | Detects and rejects duplicate task delegations within a window | 60s window |
| **Circuit breaker** | If an agent pair exceeds error/bounce threshold, block further messages until manual reset or cooldown | 3 bounces, 5min cooldown |
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

## Conflict Resolution Protocol

When two or more agents disagree on an approach (architecture, implementation,
priority), the framework provides multiple configurable resolution strategies
behind a `ConflictResolver` protocol. New strategies can be added without
modifying existing ones. The strategy is configurable per company, per
department, or per conflict type.

=== "Strategy 1: Authority + Dissent Log"

    **Default Strategy**

    The agent with higher authority level decides. Cross-department conflicts
    (incomparable authority) escalate to the lowest common manager in the
    hierarchy. The losing agent's reasoning is preserved as a **dissent record**
    -- a structured log entry containing the conflict context, both positions,
    and the resolution. Dissent records feed into organizational learning and
    can be reviewed during retrospectives.

    ```yaml
    conflict_resolution:
      strategy: "authority"            # authority, debate, human, hybrid
    ```

    - Deterministic, zero extra tokens, fast resolution
    - Dissent records create institutional memory of alternative approaches

    !!! warning "Authority deference risk (paper 1 risk 2.2)"
        [arXiv:2603.27771](https://huggingface.co/papers/2603.27771) documents a
        **100% deterministic failure mode** when authority cues are present in
        multi-agent deliberation: 0/10 errors without an authority cue flip to
        10/10 errors with the cue, same evidence, same agents. Downstream
        Auditor / Summarizer roles "lock onto" the authority signal and cease
        independent checks, and `DissentRecord` preservation alone is only a
        partial defense because downstream consumers override evidence anyway.

        This strategy is safe for **1-2 downstream agents**. For deliberation
        stacks with more than two downstream agents, `AuthorityDeferenceGuard`
        is **implemented** as agent middleware (`engine/middleware/s1_constraints.py`):
        detects authority cues in transcripts via regex patterns, stores a
        mandatory-justification header in middleware metadata for downstream
        prompt injection, and logs all detections for audit. Coordination-level
        analog scans rollup summaries before parent-task updates. See
        [S1 Multi-Agent Architecture Decision §3](../research/s1-multi-agent-decision.md#section-3--risk-mitigation-register-15-emergent-risks-from-paper-1),
        [Verification & Quality -- Harness Middleware Layer](verification-quality.md#harness-middleware-layer),
        and [#1260](https://github.com/Aureliolo/synthorg/issues/1260).

=== "Strategy 2: Structured Debate + Judge"

    Both agents present arguments (1 round each). A judge -- their shared
    manager, the CEO, or a configurable arbitrator agent -- evaluates both
    positions and decides. The judge's reasoning and both arguments are logged
    as a dissent record.

    ```yaml
    conflict_resolution:
      strategy: "debate"
      debate:
        judge: "shared_manager"        # shared_manager, ceo, designated_agent
    ```

    - Better decisions -- forces agents to articulate reasoning
    - Higher token cost, adds latency proportional to argument length

=== "Strategy 3: Human Escalation"

    All genuine conflicts go to the human approval queue with both positions
    summarized. The agent(s) park the conflicting task and work on other tasks
    while waiting (see [Approval Timeout](security.md#approval-timeout-policy)).

    ```yaml
    conflict_resolution:
      strategy: "human"
    ```

    - Safest -- human always makes the call
    - Bottleneck at scale, depends on human availability

=== "Strategy 4: Hybrid"

    **Recommended for Production**

    Combines strategies with an intelligent review layer:

    1. Both agents present arguments (1 round) -- preserving dissent
    2. A **conflict review agent** evaluates the result:
        - If the resolution is **clear** (one position is objectively better,
          or authority applies cleanly) -- resolve automatically, log dissent
          record
        - If the resolution is **ambiguous** (genuine trade-offs, no clear
          winner) -- escalate to human queue with both positions + the review
          agent's analysis

    ```yaml
    conflict_resolution:
      strategy: "hybrid"
      hybrid:
        review_agent: "conflict_reviewer"  # dedicated agent or role
        escalate_on_ambiguity: true
    ```

    - Best balance: most conflicts resolve fast, humans only see genuinely
      hard calls
    - Most complex to implement; review agent itself needs careful prompt
      design

---

## Meeting Protocol

Meetings (Pattern 3 above) follow configurable protocols that determine how
agents interact during structured multi-agent conversations. Different meeting
types naturally suit different protocols. All protocols implement a
`MeetingProtocol` protocol, making the system extensible -- new protocols can be
registered and selected per meeting type. Cost bounds are enforced by
`duration_tokens` in the [communication config](#communication-config).

!!! note "SEC-1: lateral prompt-injection defenses"
    Every protocol below carries the same prompt-injection defense: agenda
    fields (title, context, items) are wrapped in `<task-data>` and each peer
    agent's contribution is wrapped in `<peer-contribution>` before being
    interpolated into the next agent's user message. The meeting agent
    system prompt (`agent_caller._render_system_prompt`) appends the
    canonical `untrusted_content_directive` listing both fences so the model
    is told their content is untrusted data. A compromised participant
    cannot break out of its fence to hijack downstream turns or the
    leader's synthesis. See [SEC-1: Prompt Safety](../reference/sec-prompt-safety.md).

!!! warning "Synthesis risks: majority sway + authority deference"
    All three protocols below terminate their group discussion in a
    **synthesis step** that aggregates participant positions into a single
    decision. [arXiv:2603.27771](https://huggingface.co/papers/2603.27771)
    documents two distinct synthesis-time failure modes:

    - **Majority sway bias (risk 2.1)**: in a news-summarization experiment with
      7 fast-retrieval agents (wrong answer) vs. 3 deep-verification agents
      (accurate evidence), **6/10 runs** synthesized to the majority position
      despite the minority providing verifiable evidence.
    - **Authority deference (risk 2.2)**: when any one participant carries an
      authority marker, downstream synthesis locks onto the authority signal
      with 10/10 deterministic errors (see the warning on the
      [Authority + Dissent Log resolver](#conflict-resolution-protocol)).

    The current synthesizer weights positions equally and does not preserve
    minority-report positions as first-class output. The planned
    `EvidenceWeightedSynthesizer` (weight by verifiable-evidence density, cap
    correlated-source clusters, preserve minority reports in an extended
    `DissentRecord.minority_evidence` field) mitigates both risks. Tracked as
    a constraint on [#1251](https://github.com/Aureliolo/synthorg/issues/1251). See
    [S1 Multi-Agent Architecture Decision §3](../research/s1-multi-agent-decision.md#section-3--risk-mitigation-register-15-emergent-risks-from-paper-1).

=== "Protocol 1: Round-Robin Transcript"

    The meeting leader calls each participant in turn. A shared transcript
    grows as each agent responds, seeing all prior contributions. The leader
    summarizes and extracts action items at the end.

    ```yaml
    meeting_protocol: "round_robin"
    round_robin:
      max_turns_per_agent: 2
      max_total_turns: 16
      leader_summarizes: true
    ```

    - Simple, natural conversation feel, each agent sees full context
    - Token cost grows quadratically; last speaker has more context (ordering
      bias)

    Best for
    :   Daily standups, status updates, small groups (3--5 agents).

=== "Protocol 2: Async Position Papers + Synthesizer"

    Each agent independently writes a short position paper (parallel execution,
    no shared context). A synthesizer agent reads all positions, identifies
    agreements and conflicts, and produces decisions + action items.

    ```yaml
    meeting_protocol: "position_papers"
    position_papers:
      max_tokens_per_position: 300
      synthesizer: "meeting_leader"    # who synthesizes
    ```

    - Cheapest -- parallel calls, no quadratic growth, no ordering bias, no
      groupthink
    - Loses back-and-forth dialogue; agents cannot challenge each other's ideas

    Best for
    :   Brainstorming, architecture proposals, large groups, cost-sensitive
        meetings.

=== "Protocol 3: Structured Phases"

    Meeting split into phases with targeted participation:

    1. **Agenda broadcast** -- leader shares agenda and context to all
       participants
    2. **Input gathering** -- each agent submits input independently (parallel);
       strategic lens perspective injected per participant when configured
    3. **Discussion round** -- only triggered if conflicts are detected between
       inputs (pluggable conflict detection: keyword, structured comparison,
       LLM judge, hybrid, or auto-select); relevant agents debate
    4. **Premortem** *(optional)* -- participants imagine the decision failed
       and identify failure modes, risks, and hidden assumptions
    5. **Devil's advocate** *(optional)* -- injected automatically when
       consensus velocity detector identifies premature agreement
    6. **Decision + action items** -- leader synthesizes, creates tasks from
       action items

    ```yaml
    meeting_protocol: "structured_phases"
    auto_create_tasks: true              # action items become tasks (top-level, applies to any protocol)
    structured_phases:
      skip_discussion_if_no_conflicts: true
      max_discussion_tokens: 1000
    ```

    - Cost-efficient -- parallel input, discussion only when needed
    - More complex orchestration; conflict detection between inputs adds
      implementation complexity

    Best for
    :   Sprint planning, design reviews, architecture decisions.

## Meeting Scheduler

The `MeetingScheduler` is a background service that bridges meeting configuration
and execution. It reads `MeetingsConfig` and manages two modes of meeting
triggering:

### Frequency-Based Scheduling

Meetings with a `frequency` field (e.g. `daily`, `weekly`, `bi_weekly`,
`per_sprint_day`, `monthly`) are scheduled as periodic asyncio tasks. The
`MeetingFrequency` enum maps each value to a sleep interval in seconds. Periodic
tasks survive transient errors -- a single execution failure does not kill the
background loop.

### Event-Triggered Meetings

Meetings with a `trigger` field (e.g. `on_pr`, `deploy_complete`) are executed
on demand via `trigger_event(event_name, context)`. The scheduler matches all
meeting types whose `trigger` value equals the event name and executes them in
parallel using `asyncio.TaskGroup`.

### Participant Resolution

The `ParticipantResolver` protocol resolves participant reference strings from
config into concrete agent IDs. The `RegistryParticipantResolver` implementation
uses the `AgentRegistryService` with a five-step cascade:

1. **Context lookup** -- if the event context dict has a matching key, use its value.
2. **Special `"all"`** -- resolves to all active agents.
3. **Department lookup** -- resolves to all agents in the named department.
4. **Agent name lookup** -- resolves to the agent with that name.
5. **Pass-through** -- assumes the entry is a literal agent ID.

Results are deduplicated while preserving insertion order. The first resolved
participant is designated as the meeting leader.

When no `AgentRegistryService` is available (e.g. during auto-wiring without an
explicit registry), the `PassthroughParticipantResolver` is used as a fallback.
It supports only context lookup and literal pass-through (steps 1 and 5 above),
skipping the registry-dependent steps (2--4).

### Meeting API Response Enrichment

The meeting REST API enriches every `MeetingRecord` response with computed
analytics fields. Per-participant metrics are derived from
`MeetingMinutes.contributions`:

- **`token_usage_by_participant`** (`dict[str, int]`): total tokens (input +
  output) consumed per agent. Empty when no minutes are available.
- **`contribution_rank`** (`tuple[str, ...]`): agent IDs sorted by total token
  usage descending. Empty when no minutes are available.

Duration is computed from the meeting timestamps, not from contributions:

- **`meeting_duration_seconds`** (`float | null`, `>= 0.0`): duration computed
  from `ended_at - started_at`, clamped to `0.0` when negative. `null` when no
  minutes are available.

These fields are applied to all meeting endpoints (list, detail, trigger).

### Auto-Wiring

The `MeetingOrchestrator` is auto-wired at startup alongside Phase 1
services (no persistence dependency). All three meeting protocols are
registered with default configs.

**Fully-wired mode.** When both `agent_registry` and `provider_registry`
are available, the `agent_caller` dispatches a real LLM call per turn
(one `provider.complete()` per agent per turn, with automatic retry +
rate limiting via `BaseCompletionProvider`). The `MeetingScheduler` and
`CeremonyScheduler` are auto-wired alongside the orchestrator so
periodic and event-triggered meetings run on schedule.

**Degraded (unconfigured) mode.** When either `agent_registry` or
`provider_registry` is missing, the orchestrator is still constructed
so REST endpoints stay available, but:

- The `agent_caller` returned by `build_unconfigured_meeting_agent_caller`
  raises `MeetingAgentCallerNotConfiguredError` at call time -- no
  silent empty responses.
- `MeetingScheduler` and `CeremonyScheduler` are **not** auto-wired
  (`meeting_wire.meeting_scheduler is None`,
  `meeting_wire.ceremony_scheduler is None`).  Running scheduled
  meetings against a known-failing caller would only produce background
  noise, so periodic and ceremony-triggered meetings are skipped
  entirely until the missing dependencies are provided.

This forces operators to surface wiring gaps instead of producing
meaningless participation, and prevents the schedulers from spamming
logs with avoidable failures during degraded startup.

## MCP Service Facades

The communication domain exposes five service facades on `AppState` for
MCP handler shims. Each is a thin wrapper; audit logging lives in the
facade, not the handler or the repository.

| Facade | Module | Tools shimmed |
|---|---|---|
| `MessageService` | `synthorg.communication.messages.service` | `synthorg_messages_list`/`_get`/`_send`/`_delete` |
| `MeetingService` | `synthorg.communication.meetings.service` | `synthorg_meetings_list`/`_get`/`_create`/`_update`/`_delete` |
| `ConnectionService` | `synthorg.integrations.connections.mcp_service` | `synthorg_connections_list`/`_get`/`_create`/`_delete`/`_check_health` |
| `WebhookService` | `synthorg.integrations.webhooks.service` | `synthorg_webhooks_list`/`_get`/`_create`/`_update`/`_delete` |
| `TunnelService` | `synthorg.integrations.tunnel.mcp_service` | `synthorg_tunnel_get_status`/`_connect` |

See `docs/design/tools.md` §"SynthOrg MCP Tool Surface" for the handler
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

### Meeting Protocol Safety

All three meeting protocols (StructuredPhases, RoundRobin, PositionPapers) guarantee
bounded execution via `TokenTracker` phase-boundary checks, hard token budgets with 20%
synthesis reserve, and turn/round limits. No protocol has unbounded execution paths.

**Meeting-task feedback loop mitigation**: `MeetingProtocolConfig.auto_create_tasks`
defaults to `True`. Two guardrails prevent runaway task/meeting cycles:
`MeetingTypeConfig.min_interval_seconds` enforces per-type cooldown on event-triggered
meetings, and `MeetingProtocolConfig.max_tasks_per_meeting` caps task creation from
action items. See #1115.

### Conflict Resolution Termination

All four conflict resolution strategies terminate with bounded resource use:

- **AuthorityResolver**: Deterministic seniority comparison. Always terminates; no LLM calls.
- **DebateResolver**: Single LLM judge call (one-shot, no retry loop). Falls back to
  Authority if no evaluator configured, or if the evaluator raises an exception (#1117).
- **HumanEscalationResolver**: Persists the escalation to a pluggable queue
  backend (in-memory / SQLite / Postgres), dispatches a
  `NotificationCategory.ESCALATION` to operators, and awaits the operator
  decision via an in-process ``asyncio.Future`` registered in
  ``PendingFuturesRegistry``. On timeout (bounded by
  ``EscalationQueueConfig.default_timeout_seconds``, ``None`` = wait forever)
  the row is marked ``EXPIRED`` and the resolver returns an
  ``ESCALATED_TO_HUMAN`` outcome so downstream callers always receive a
  terminal ``ConflictResolution``. Operators collect and decide via the
  ``/conflicts/escalations`` REST surface (#1418).

    **Multi-worker wake-up (#1444):** ``PendingFuturesRegistry`` is
    process-local by design.  When the API runs across multiple workers
    or pods sharing a Postgres backend, a decision submitted through
    worker B must still wake a resolver blocked on worker A.  The queue
    wires this via Postgres ``LISTEN`` / ``NOTIFY``: the Postgres
    repository publishes ``<id>:<status>`` on the
    ``conflict_escalation_events`` channel from the *application* side
    after every terminal transition (``mark_decided``, ``mark_expired``,
    ``cancel``) -- no database trigger is installed, so operators need
    no elevated privileges to ship the schema.  An
    ``EscalationNotifySubscriber`` running in each worker listens on
    that channel and forwards the signal to its local registry.  The
    subscriber is controlled by
    ``EscalationQueueConfig.cross_instance_notify`` (``auto`` -- default,
    enables it automatically for the Postgres backend; ``on`` -- force
    it, fail startup if the backend cannot support it; ``off`` -- scope
    to a single worker).

    **Timeout re-read fallback.** Because the NOTIFY publish is
    app-side and best-effort, a subscriber restart, network blip, or
    deployment rollover can drop the wake-up for an in-flight
    resolver.  To keep the decision path correct under those windows,
    ``HumanEscalationResolver`` re-reads the escalation row on
    ``TimeoutError`` and, if it finds a persisted ``DECIDED`` payload,
    hands the operator's decision to the processor instead of
    returning the generic ``ESCALATED_TO_HUMAN`` fallback.  The
    sweeper and per-resolver timeout still bound stale rows; the
    re-read guarantees that an operator's choice is never masked by a
    missed notification.

    **Schema-level invariants.** The ``conflict_escalations`` table
    enforces three CHECK constraints that together make impossible
    row shapes unrepresentable: (1) ``DECIDED`` requires the full
    ``decision_json`` / ``decided_at`` / ``decided_by`` triple, (2)
    ``PENDING`` forbids all three, (3) ``EXPIRED`` / ``CANCELLED``
    forbid ``decision_json``.  A partial unique index on ``conflict_id
    WHERE status = 'pending'`` enforces "at most one active escalation
    per conflict", and a ``(status, expires_at)`` index backs the
    sweeper's hot ``mark_expired`` query.
- **HybridResolver**: Single LLM review call; deterministic fallback to Authority on ambiguity.

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
| Chatty interfaces | Low -- detected via `MessageOverhead.is_quadratic` | Detection exists; no enforcement circuit breaker |
| Distributed monolith | None -- async pull message bus, no synchronous coupling | |
| Ownership ambiguity | None -- TaskEngine single-writer actor | |
| Cascading failure | Low -- `fail_fast` bounds wave propagation | No upstream contamination detection |

---

## Event Stream & HITL Surface

*Implemented in #1263. SSE event stream for dashboard observability and
human-in-the-loop (HITL) interrupt/resume protocol.*

### AG-UI Projection Model

Internal observability events (from `observability/events/`) are projected
one-way to [AG-UI protocol](https://github.com/ag-ui-protocol/ag-ui)
standard types for external consumers. The internal event namespace remains
canonical -- AG-UI is the external-facing projection only.

The `EventProjector` in `communication/event_stream/projector.py` maps
internal event constants to `AgUiEventType` values:

| Internal Event | AG-UI Type |
|---|---|
| `execution.engine.start` | `run_started` |
| `execution.engine.complete` | `run_finished` |
| `execution.engine.error` | `run_error` |
| `execution.plan.step_start` | `step_started` |
| `execution.plan.step_complete` | `step_finished` |
| `execution.plan.step_failed` | `step_failed` |
| `execution.loop.turn_start` | `text_message_start` |
| `execution.loop.turn_complete` | `text_message_end` |
| `execution.loop.tool_calls` | `tool_call_start` |
| `approval_gate.context.parked` | `approval_interrupt` |
| `approval_gate.context.resumed` | `approval_resumed` |
Streaming events (`text_message_content`, `tool_call_args`, `tool_call_end`,
`info_request_interrupt`, `info_request_resumed`) and `synthorg:dissent` are
emitted directly by their services via `EventStreamHub.publish_raw()`, not via
the EventProjector log projection, because they carry structured payloads that
don't originate from a single log call.

### SSE Endpoint

`GET /api/v1/events/stream?session_id={id}` returns a `text/event-stream`
response. Each SSE event has:

```json
{
  "id": "evt-<uuid>",
  "type": "<AgUiEventType>",
  "timestamp": "<ISO 8601>",
  "session_id": "<session>",
  "correlation_id": "<optional>",
  "agent_id": "<optional>",
  "payload": { ... }
}
```

The `EventStreamHub` (`communication/event_stream/stream.py`) is the single
pub/sub source. Both the AG-UI dashboard and the A2A gateway consume
from this hub, each applying their own projection layer.

### Interrupt / Resume Protocol

Two blocking interrupt types:

**Tool Approval Interrupt** -- emitted when `ApprovalGate` parks execution:

- Payload: `interrupt_id`, `tool_name`, `tool_args`, `evidence_package_id`,
  `timeout_seconds`
- Resume: `POST /api/v1/events/resume/{interrupt_id}` with
  `{decision, feedback}`

**Information Request Interrupt** -- emitted when an agent needs
mid-task clarification:

- Payload: `interrupt_id`, `question`, `context_snippet`, `timeout_seconds`
- Resume: `POST /api/v1/events/resume/{interrupt_id}` with `{response}`

Non-SSE polling fallback for CLI/integration tests:
`GET /api/v1/interrupts` + `POST /api/v1/interrupts/{id}/resume`.

### EvidencePackage Schema

`EvidencePackage` (in `core/evidence.py`, re-exported from
`communication/event_stream/evidence.py`) is the structured HITL approval
payload. It extends `StructuredArtifact` (shared base with
`HandoffArtifact` from R2 #1262):

- `id`, `title`, `narrative` -- human-readable summary
- `reasoning_trace` -- compressed reasoning steps
- `recommended_actions` -- 1-3 `RecommendedAction` options
- `risk_level` -- `ApprovalRiskLevel`
- `source_agent_id`, `task_id`, `metadata`

`ApprovalItem.evidence_package` (optional) carries the package; existing
approval paths can adopt incrementally.

**Quantum-safe signing**: High-risk `EvidencePackage` approvals
(`risk_level >= HIGH`) use m-of-n threshold signing via the
[Quantum-Safe Audit Trail](security.md#quantum-safe-audit-trail).
`EvidencePackageSignature` carries ML-DSA-65 signatures; the
`is_fully_signed` computed field checks the threshold.
See `src/synthorg/observability/audit_chain/` for the signing
infrastructure.

### DissentRecord as First-Class Message Type

`MessageType.DISSENT` promotes `DissentRecord` from a persistence-only
artifact to a typed message on the bus (S1 #1254 constraint). When
a conflict is resolved:

1. Dissent records are built for overruled positions (existing)
2. A `synthorg:dissent` SSE event is published via the `EventStreamHub`
3. `COMM_DISSENT_PUBLISHED` observability event is logged

### A2A Projection Consolidation

The `EventStreamHub` is the single event source for all consumers. The
A2A gateway subscribes to the same hub and applies A2A-specific state
mapping (see [A2A External Gateway](#a2a-external-gateway) above) as a
separate projection layer. No second SSE backend is needed.

## Async Delegation

Supervisor agents manage background subagent tasks without blocking their own
execution loop. The async task protocol provides five steering tools that wrap
the existing `TaskEngine` -- no parallel task system is created.

### Steering Tools

| Tool | Service Method | Effect |
|------|---------------|--------|
| `start_async_task` | `AsyncTaskService.start_async_task()` | Creates + assigns a task via `TaskEngine`, returns task ID |
| `check_async_task` | `AsyncTaskService.check_async_task()` | Projects `TaskEngine` state to `AsyncTaskStatus` |
| `update_async_task` | `AsyncTaskService.update_async_task()` | Posts `CONTEXT_INJECTION` message to executing agent via `MessageBus` |
| `cancel_async_task` | `AsyncTaskService.cancel_async_task()` | Cancels task via `TaskEngine` with reason `ASYNC_CANCEL` |
| `list_async_tasks` | `AsyncTaskService.list_async_tasks()` | Returns `(task_id, status)` pairs for child tasks by `parent_task_id` |

All five are registered under the `communication.async_tasks` namespace
and gated by `ToolPermission.DELEGATION`.

### State Channel Pattern

`AgentContext.async_task_state` is a dedicated `AsyncTaskStateChannel`
that holds `AsyncTaskRecord` entries. It is structurally separate from
`AgentContext.conversation` -- compaction strategies and
`ContextResetMiddleware` (R1 #1260) do not touch it. The state channel
is projected into the agent's system prompt on each turn via
`_inject_async_task_section()`, appended after trimming so it is never
trimmed away.

### AsyncTaskService Wraps TaskEngine

`AsyncTaskService` is a thin facade over `TaskEngine`:

- Tasks are created via `TaskEngine.create_task()` with `parent_task_id`
  for lineage, then transitioned to `ASSIGNED` with the target agent
- Status is projected through `_STATUS_MAP` (internal `TaskStatus` to
  supervisor-facing `AsyncTaskStatus`)
- Context injection uses `MessageBus.send_direct()` with
  `MessageType.CONTEXT_INJECTION`
- Listing filters `TaskEngine.list_tasks()` by `parent_task_id`

### `max_delegation_rounds` on `CoordinationConfig`

Soft cap (default 3) emits `DELEGATION_ROUND_SOFT_LIMIT` warning.
Hard abort at 2x soft cap (default 6) raises `DelegationRoundLimitError`.
Prevents delegation runaway in multi-hop delegation chains.

### Citation Tracking

Research tasks need deduplicated citation tracking across parallel
sub-agent findings.

`Citation` is a frozen Pydantic model with `url` (canonical normalized
form), `title`, `first_seen_at`, `first_seen_by_agent_id`, and
`accessed_via` (tool/memory/file).

`CitationManager` is immutable (each operation returns a new instance).
It tracks citations by normalized URL, deduplicating across agents:

- `add()` normalizes the URL and deduplicates against existing entries
- `render_inline()` returns `[N]` for a tracked URL
- `render_sources_section()` renders the final `## Sources` block
- `to_handoff_payload()` / `from_handoff_payload()` enable propagation
  through delegation chains via `HandoffArtifact`

URL normalization (`normalize_url()`) lowercases scheme + host, strips
default ports, drops fragment and credentials, sorts query parameters,
strips trailing slash, and wraps IPv6 addresses in brackets.
