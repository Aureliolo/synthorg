---
title: Communication
description: Message bus architecture, delegation, conflict resolution strategies, and meeting protocols for inter-agent communication.
---

# Communication

The communication architecture defines how agents exchange information, resolve
disagreements, and coordinate through structured meetings. All communication
patterns, conflict resolution strategies, and meeting protocols are pluggable
and configurable per company, per department, or per interaction type.

---

## Communication Patterns

The framework supports multiple communication patterns, configurable per company:

=== "Pattern 1: Event-Driven Message Bus"

    **Recommended Default**

    ```text
    ┌──────────┐     ┌─────────────────┐     ┌──────────┐
    │  Agent A  │────>│   Message Bus    │<────│  Agent B  │
    └──────────┘     │  (Topics/Queues) │     └──────────┘
                     └────────┬────────┘
                              │
                  ┌───────────┼───────────┐
                  v           v           v
            #engineering  #product   #all-hands
            #code-review  #design    #incidents
    ```

    - Agents publish to topics, subscribe to relevant channels
    - Async by default, enables parallelism
    - Decoupled -- agents do not need to know about each other
    - Natural audit trail of all communications

    Best for
    :   Most scenarios; scales well, production-ready pattern.

=== "Pattern 2: Hierarchical Delegation"

    ```text
    CEO --> CTO --> Eng Lead --> Sr Dev --> Jr Dev
                       |
                       └--> QA Lead --> QA Eng
    ```

    - Tasks flow down the hierarchy, results flow up
    - Each level can decompose and refine tasks before delegating
    - Authority enforcement built into the flow

    Best for
    :   Structured organizations with clear chains of command.

=== "Pattern 3: Meeting-Based"

    ```text
    ┌─────────────────────────────────┐
    │        Sprint Planning          │
    │  PM + CTO + Devs + QA + Design │
    │  Output: Sprint backlog         │
    └─────────────────────────────────┘
             │
    ┌────────┴────────┐
    │  Daily Standup  │
    │  Devs + QA      │
    │  Output: Status │
    └─────────────────┘
    ```

    - Structured multi-agent conversations at defined intervals
    - Standup, sprint planning, retrospective, design review, code review

    Best for
    :   Agile workflows, decision-making, alignment.

=== "Pattern 4: Hybrid"

    **Recommended for Full Company**

    Combines all three patterns:

    - **Message bus** for async daily work and notifications
    - **Hierarchical delegation** for task assignment and approvals
    - **Meetings** for cross-team decisions and planning ceremonies

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

## Message Format

```json
{
  "id": "msg-uuid",
  "timestamp": "2026-02-27T10:30:00Z",
  "from": "sarah_chen",
  "to": "engineering",
  "type": "task_update",
  "priority": "normal",
  "channel": "#backend",
  "content": "Completed API endpoint for user authentication. PR ready for review.",
  "attachments": [
    {"type": "artifact", "ref": "pr-42"}
  ],
  "metadata": {
    "task_id": "task-123",
    "project_id": "proj-456",
    "tokens_used": 1200,
    "cost_usd": 0.018
  }
}
```

---

## Communication Config

???+ example "Full communication configuration"

    ```yaml
    communication:
      default_pattern: "hybrid"
      message_bus:
        backend: "internal"        # internal, redis, rabbitmq, kafka
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
      ancestry_tracking: true       # always on, not configurable
    ```

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
    while waiting (see [Approval Timeout](operations.md#approval-timeout-policy)).

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
    2. **Input gathering** -- each agent submits input independently (parallel)
    3. **Discussion round** -- only triggered if conflicts are detected between
       inputs; relevant agents debate (1 round, capped tokens)
    4. **Decision + action items** -- leader synthesizes, creates tasks from
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
