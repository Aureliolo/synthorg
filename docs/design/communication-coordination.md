---
title: Communication Coordination
description: Loop prevention, conflict resolution, meeting protocols, MCP service facades, and multi-agent failure pattern guardrails.
---

# Communication Coordination

How agents coordinate during multi-agent interactions: delegation loops are blocked,
disagreements resolve through configurable strategies, structured meetings follow one
of three protocols, and known multi-agent failure modes carry explicit guardrails.

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
    (a structured log entry containing the conflict context, both positions,
    and the resolution). Dissent records feed into organisational learning and
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
        partial defence because downstream consumers override evidence anyway.

        This strategy is safe for **1-2 downstream agents**. For deliberation
        stacks with more than two downstream agents, `AuthorityDeferenceGuard`
        is **implemented** as agent middleware (`engine/middleware/s1_constraints.py`):
        detects authority cues in transcripts via regex patterns, stores a
        mandatory-justification header in middleware metadata for downstream
        prompt injection, and logs all detections for audit. Coordination-level
        analog scans rollup summaries before parent-task updates. See
        [S1 Multi-Agent Architecture Decision §3](../research/s1-multi-agent-decision.md#section-3-risk-mitigation-register-15-emergent-risks-from-paper-1),
        [Verification & Quality: Harness Middleware Layer](verification-quality.md#harness-middleware-layer),
        and [#1260](https://github.com/Aureliolo/synthorg/issues/1260).

=== "Strategy 2: Structured Debate + Judge"

    Both agents present arguments (1 round each). A judge (their shared
    manager, the CEO, or a configurable arbitrator agent) evaluates both
    positions and decides. The judge's reasoning and both arguments are logged
    as a dissent record.

    ```yaml
    conflict_resolution:
      strategy: "debate"
      debate:
        judge: "shared_manager"        # shared_manager, ceo, designated_agent
    ```

    - Better decisions: forces agents to articulate reasoning
    - Higher token cost, adds latency proportional to argument length

=== "Strategy 3: Human Escalation"

    All genuine conflicts go to the human approval queue with both positions
    summarised. The agent(s) park the conflicting task and work on other tasks
    while waiting (see [Approval Timeout](security.md#approval-timeout-policy)).

    ```yaml
    conflict_resolution:
      strategy: "human"
    ```

    - Safest: human always makes the call
    - Bottleneck at scale, depends on human availability

=== "Strategy 4: Hybrid"

    **Recommended for Production**

    Combines strategies with an intelligent review layer:

    1. Both agents present arguments (1 round), preserving dissent
    2. A **conflict review agent** evaluates the result:
        - If the resolution is **clear** (one position is objectively better,
          or authority applies cleanly): resolve automatically, log dissent
          record
        - If the resolution is **ambiguous** (genuine trade-offs, no clear
          winner): escalate to human queue with both positions + the review
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

Meetings (Pattern 3 of [Communication Patterns](communication.md#communication-patterns)) follow configurable protocols that determine how
agents interact during structured multi-agent conversations. Different meeting
types naturally suit different protocols. All protocols implement a
`MeetingProtocol` protocol, making the system extensible; new protocols can be
registered and selected per meeting type. Cost bounds are enforced by
`duration_tokens` in the [communication config](communication.md#communication-config).

!!! note "SEC-1: lateral prompt-injection defences"
    Every protocol below carries the same prompt-injection defence: agenda
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
      (accurate evidence), **6/10 runs** synthesised to the majority position
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
    [S1 Multi-Agent Architecture Decision §3](../research/s1-multi-agent-decision.md#section-3-risk-mitigation-register-15-emergent-risks-from-paper-1).

=== "Protocol 1: Round-Robin Transcript"

    The meeting leader calls each participant in turn. A shared transcript
    grows as each agent responds, seeing all prior contributions. The leader
    summarises and extracts action items at the end.

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

    - Cheapest: parallel calls, no quadratic growth, no ordering bias, no
      groupthink
    - Loses back-and-forth dialogue; agents cannot challenge each other's ideas

    Best for
    :   Brainstorming, architecture proposals, large groups, cost-sensitive
        meetings.

=== "Protocol 3: Structured Phases"

    Meeting split into phases with targeted participation:

    1. **Agenda broadcast**: leader shares agenda and context to all
       participants
    2. **Input gathering**: each agent submits input independently (parallel);
       strategic lens perspective injected per participant when configured
    3. **Discussion round**: only triggered if conflicts are detected between
       inputs (pluggable conflict detection: keyword, structured comparison,
       LLM judge, hybrid, or auto-select); relevant agents debate
    4. **Premortem** *(optional)*: participants imagine the decision failed
       and identify failure modes, risks, and hidden assumptions
    5. **Devil's advocate** *(optional)*: injected automatically when
       consensus velocity detector identifies premature agreement
    6. **Decision + action items**: leader synthesises, creates tasks from
       action items

    ```yaml
    meeting_protocol: "structured_phases"
    auto_create_tasks: true              # action items become tasks (top-level, applies to any protocol)
    structured_phases:
      skip_discussion_if_no_conflicts: true
      max_discussion_tokens: 1000
    ```

    - Cost-efficient: parallel input, discussion only when needed
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
tasks survive transient errors; a single execution failure does not kill the
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

1. **Context lookup**: if the event context dict has a matching key, use its value.
2. **Special `"all"`**: resolves to all active agents.
3. **Department lookup**: resolves to all agents in the named department.
4. **Agent name lookup**: resolves to the agent with that name.
5. **Pass-through**: assumes the entry is a literal agent ID.

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
  raises `MeetingAgentCallerNotConfiguredError` at call time; no
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

### Meeting Protocol Safety

All three meeting protocols (StructuredPhases, RoundRobin, PositionPapers) guarantee
bounded execution via `TokenTracker` phase-boundary checks, hard token budgets with 20%
synthesis reserve, and turn/round limits. No protocol has unbounded execution paths.

**Meeting state-transition logs.** Per the CLAUDE.md state-transition contract, the
orchestrator emits `MEETING_COMPLETED` / `MEETING_FAILED` / `MEETING_BUDGET_EXHAUSTED` /
`MEETING_CANCELLED` at INFO/WARNING/ERROR level **after** the corresponding `MeetingRecord`
is appended to `self._records`. Logs only fire for transitions that actually landed; if
record assembly raises (model_validate, memory pressure), the log is skipped so the audit
trail stays consistent with the in-memory record store.

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

    **Multi-worker wake-up (PR #1444):** ``PendingFuturesRegistry`` is
    process-local by design. When the API runs across multiple workers
    or pods sharing a Postgres backend, a decision submitted through
    worker B must still wake a resolver blocked on worker A. The queue
    wires this via Postgres ``LISTEN`` / ``NOTIFY``: the Postgres
    repository publishes ``<id>:<status>`` on the
    ``conflict_escalation_events`` channel from the *application* side
    after every terminal transition (``mark_decided``, ``mark_expired``,
    ``cancel``); no database trigger is installed, so operators need
    no elevated privileges to ship the schema. An
    ``EscalationNotifySubscriber`` running in each worker listens on
    that channel and forwards the signal to its local registry. The
    subscriber is controlled by
    ``EscalationQueueConfig.cross_instance_notify`` (``auto``: default,
    enables it automatically for the Postgres backend; ``on``: force
    it, fail startup if the backend cannot support it; ``off``: scope
    to a single worker).

    **Timeout re-read fallback.** Because the NOTIFY publish is
    app-side and best-effort, a subscriber restart, network blip, or
    deployment rollover can drop the wake-up for an in-flight
    resolver. To keep the decision path correct under those windows,
    ``HumanEscalationResolver`` re-reads the escalation row on
    ``TimeoutError`` and, if it finds a persisted ``DECIDED`` payload,
    hands the operator's decision to the processor instead of
    returning the generic ``ESCALATED_TO_HUMAN`` fallback. The
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
| Chatty interfaces | Low; detected via `MessageOverhead.is_quadratic` | Detection exists; no enforcement circuit breaker |
| Distributed monolith | None; async pull message bus, no synchronous coupling | |
| Ownership ambiguity | None; TaskEngine single-writer actor | |
| Cascading failure | Low; `fail_fast` bounds wave propagation | No upstream contamination detection |
