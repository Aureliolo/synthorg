---
title: Agent Execution
description: Agent execution status, execution loops (ReAct, Plan-and-Execute, Hybrid), prompt profiles, stagnation detection, context budget management, context compaction, brain / hands / session semantics, and ACG vocabulary.
---

# Agent Execution

This page covers the agent-side execution plane: how a single agent runs a task. The engine dispatches work via the [TaskEngine](engine.md#taskengine-centralized-state-coordination); the agent receives it, enters an execution loop, and iterates through LLM turns and tool calls until completion or handoff. The loop type, prompt profile, stagnation guards, and context-budget policies are all pluggable per agent.

## Agent Execution Status

The `ExecutionStatus` enum (in `engine/agent_state.py`) tracks the per-agent
runtime execution state:

| Status | Meaning |
|--------|---------|
| `IDLE` | Agent is not currently executing; no active task or execution run. |
| `EXECUTING` | Agent is actively processing a task within an execution loop. |
| `PAUSED` | Agent is waiting for an external event (e.g. approval gate). |

`ExecutionStatus` is consumed by `AgentRuntimeState` (in `engine/agent_state.py`),
which is persisted via `AgentStateRepository` for dashboard queries and
graceful-shutdown discovery. See the [Agents design page](agents.md#runtime-state)
for how `AgentRuntimeState` fits into the runtime state layer.

## Agent Execution Loop

The agent execution loop defines how an agent processes a task from start to
finish. The framework provides multiple configurable loop architectures behind
an `ExecutionLoop` protocol, making the system extensible. The default can vary
by task complexity and is configurable per agent or role.

### ExecutionLoop Protocol

All loop implementations satisfy the `ExecutionLoop` runtime-checkable protocol:

`get_loop_type() -> str`
:   Returns a unique identifier (e.g., `"react"`).

`execute(...) -> ExecutionResult`
:   Runs the loop to completion, accepting `AgentContext`,
    `CompletionProvider`, optional `ToolInvoker`, optional `BudgetChecker`,
    optional `ShutdownChecker`, and optional `CompletionConfig`.

**Supporting models:**

`TerminationReason`
:   Enum: `COMPLETED`, `MAX_TURNS`, `BUDGET_EXHAUSTED`, `SHUTDOWN`, `STAGNATION`,
    `ERROR`, `PARKED`, `CANCELLED`.  `max_turns` defaults to 20.  `CANCELLED`
    fires when a per-task `TaskCancellationChecker` observes the task's terminal
    status at a safe boundary (e.g. an operator superseded it via mid-flight
    steering); the loop halts and the post-execution pipeline performs no
    re-transition because the task is already terminal.

`TurnRecord`
:   Frozen per-turn stats (tokens, cost, tool calls, finish reason).

`ExecutionResult`
:   Frozen outcome with final context, termination reason, turn records, and
    optional error message (required when reason is `ERROR`).

`BudgetChecker`
:   Callback type `Callable[[AgentContext], bool]` invoked before each LLM call.

`ShutdownChecker`
:   Callback type `Callable[[], bool]` checked at turn boundaries to initiate
    cooperative shutdown.

### Loop Implementations

=== "Loop 1: ReAct"

    **Default for Simple Tasks**

    A single interleaved loop: the agent reasons about the current state,
    selects an action (tool call or response), observes the result, and repeats
    until done or `max_turns` is reached.

    ```mermaid
    graph LR
        A[Think] --> B[Act]
        B --> C[Observe]
        C --> A
        C --> D{Terminate?}
        D -->|task complete, max turns,<br/>budget exhausted, or error| E[Done]
    ```

    ```yaml
    execution_loop: "react"              # react, plan_execute, hybrid, auto
    ```

    | | |
    |---|---|
    | **Strengths** | Simple, proven, flexible. Easy to implement. Works well for short tasks. |
    | **Weaknesses** | Token-heavy on long tasks (re-reads full context every turn). No long-term planning; greedy step-by-step. |
    | **Best for** | Simple tasks, quick fixes, single-file changes. |

=== "Loop 2: Plan-and-Execute"

    A two-phase approach: the agent first generates a step-by-step plan, then
    executes each step sequentially. On failure, the agent can replan. Different
    models can be used for planning vs execution (e.g., large model for
    planning, small model for execution steps).

    ```mermaid
    graph LR
        A[Plan<br/>1 call] --> B[Execute Steps<br/>N calls]
        B --> C{Step failed?}
        C -->|yes| A
        C -->|no| D[Done]
    ```

    ```yaml
    execution_loop: "plan_execute"
    plan_execute:
      planner_model: null              # null = use agent's model; override for cost optimization
      executor_model: null
      max_replans: 3
    ```

    | | |
    |---|---|
    | **Strengths** | Token-efficient for long tasks. Auditable plan artifact. Supports model tiering. |
    | **Weaknesses** | Rigid; plan may be wrong, replanning is expensive. Over-plans simple tasks. |
    | **Best for** | Complex multi-step tasks, epic-level work, tasks spanning multiple files. |

=== "Loop 3: Hybrid Plan + ReAct Steps"

    **Recommended for Complex Tasks**

    The agent creates a high-level plan (3 to 7 steps). Each step is executed as a
    mini-ReAct loop with its own turn limit. After each step, the agent
    checkpoints, summarising progress and optionally replanning remaining
    steps. Checkpoints are natural points for human inspection or task
    suspension.

    ```mermaid
    graph TD
        A[Plan] --> B[Step 1: mini-ReAct]
        B --> C[Checkpoint: summarize progress]
        C --> D[Step 2: mini-ReAct]
        D --> E[Checkpoint: replan if needed]
        E --> F[Step N: mini-ReAct]
        F --> G[Done]
    ```

    ```yaml
    execution_loop: "hybrid"
    hybrid:
      planner_model: null
      executor_model: null
      max_plan_steps: 7
      max_turns_per_step: 5
      max_replans: 3
      checkpoint_after_each_step: true
      allow_replan_on_completion: true
    ```

    | | |
    |---|---|
    | **Strengths** | Strategic planning + tactical flexibility. Natural checkpoints for suspension/inspection. |
    | **Weaknesses** | Most complex to implement. Plan granularity needs tuning per task type. |
    | **Best for** | Complex tasks, multi-file refactoring, tasks requiring both planning and adaptivity. |

!!! tip "Auto-selection"
    When `execution_loop: "auto"`, the framework selects the loop via three
    layers:

    1. **Rule matching**: maps `estimated_complexity` to a loop type:
       simple -> ReAct, medium -> Plan-and-Execute, complex/epic -> Hybrid.
       Configurable via `AutoLoopConfig.rules` (a tuple of `AutoLoopRule`).
       When no rule matches, falls back to `default_loop_type` (default:
       react).  All loop types in rules, `hybrid_fallback`, and
       `default_loop_type` are validated against the known set at
       construction time.
    2. **Budget-aware downgrade**: when monthly budget utilisation is at
       or above `budget_tight_threshold` (default 80%), hybrid selections
       are downgraded to plan_execute to conserve budget.
    3. **Hybrid fallback**: when `hybrid_fallback` is set (default:
       `None`), redirects hybrid selections to the specified loop type.
       With `None` (default), the hybrid loop runs directly.

### AgentEngine Orchestrator

`AgentEngine` is the top-level entry point for running an agent on a task. It
composes the execution loop with prompt construction, context management, tool
invocation, and cost tracking into a single `run()` call. When an
`auto_loop_config` is provided (mutually exclusive with `execution_loop`),
the engine dynamically selects the loop per task via `_resolve_loop()`.
Optional `plan_execute_config`, `hybrid_loop_config`, and
`compaction_callback` are forwarded to the auto-selected loop so it
receives the same configuration as a statically configured loop.

The engine also exposes an optional ``coordinate()`` method that delegates to a
``MultiAgentCoordinator`` when one is configured (see [Coordination](coordination.md)).

**Signature:**

```python
async run(
    identity, task, completion_config?, max_turns?,
    memory_messages?, timeout_seconds?, effective_autonomy?
) -> AgentRunResult
```

**Pipeline steps:**

1. **Validate inputs**: agent must be `ACTIVE`, task must be `ASSIGNED` or
   `IN_PROGRESS`. Raises `ExecutionStateError` on violation.
2. **Pre-flight budget enforcement**: if `BudgetEnforcer` is provided, check
   monthly hard stop and daily limit via `check_can_execute()`, then apply
   auto-downgrade via `resolve_model()`. Raises `BudgetExhaustedError` or
   `DailyLimitExceededError` on violation. See
   [Budget & Cost Management](budget.md#cost-controls) for the full
   pre-flight / in-flight / task-boundary enforcement model.
3. **Project validation**: if `ProjectRepository` is provided, validate that the
   task's project exists (`ProjectNotFoundError` if not) and that the agent is a
   member of the project team (`ProjectAgentNotMemberError` if not; empty teams
   allow any agent). When the project has a non-zero budget and `BudgetEnforcer`
   is available, check project-level budget via `check_project_budget()`. Raises
   `ProjectBudgetExhaustedError` when the project's accumulated cost has reached
   its budget. Pre-flight project budget checks are best-effort under concurrency
   (TOCTOU); the in-flight `BudgetChecker` closure provides the true safety net.
4. **Build system prompt**: calls `build_system_prompt()` with agent identity,
   task, and resolved model tier. The tier determines a `PromptProfile` that
   controls prompt verbosity (see [Prompt Profiles](#prompt-profiles) below),
   including personality token trimming when the section exceeds the profile's
   `max_personality_tokens` budget. Trimming metadata is returned in
   `SystemPrompt.personality_trim_info`.
   Tool definitions are NOT included in the prompt; they are supplied via the
   API's `tools` parameter ([Decision Log](../architecture/decisions.md) D22).
   Follows the **non-inferable-only principle**: system prompts include only
   information the agent cannot discover by reading the codebase or environment
   (role constraints, custom conventions, organisational policies).
5. **Create context**: `AgentContext.from_identity()` with the configured
   `max_turns`.
6. **Seed conversation**: injects system prompt, optional memory messages, and
   formatted task instruction as initial messages.
7. **Transition task**: `ASSIGNED` -> `IN_PROGRESS` (pass-through if already
   `IN_PROGRESS`).
8. **Prepare tools and budget**: creates `ToolInvoker` from registry and
   `BudgetChecker` from `BudgetEnforcer` (task + monthly + daily + project limits
   with pre-computed baselines and alert deduplication) or from task budget limit
   alone when no enforcer is configured.
9. **Resolve execution loop**: if `auto_loop_config` is set, calls
   `select_loop_type()` with the task's `estimated_complexity` and current
   budget utilisation (via `BudgetEnforcer.get_budget_utilization_pct()`).
   Budget-aware downgrade: hybrid is downgraded to plan_execute when
   utilisation >= threshold. Optional hybrid fallback applies when
   `hybrid_fallback` is configured. When no auto config is set, uses
   the statically configured loop. The auto-selected loop receives the
   engine's `compaction_callback`, `plan_execute_config` (for
   plan-execute), and `hybrid_loop_config` (for hybrid), along with the
   approval gate and stagnation detector.
10. **Delegate to loop**: calls `ExecutionLoop.execute()` with context,
   provider, tool invoker, budget checker, and completion config. If
   `timeout_seconds` is set, wraps the call in `asyncio.wait`; on expiry
   the run returns with `TerminationReason.ERROR` but cost recording and
   post-execution processing still occur.
   When escalations are detected after tool execution (via
   `ToolInvoker.pending_escalations`), the `ApprovalGate` evaluates whether
   parking is needed. If so, the context is serialised via `ParkService`
   and persisted when a `ParkedContextRepository` is configured; the loop
   then returns a `PARKED` result. When an `EventStreamHub` is configured,
   the gate also emits an `APPROVAL_INTERRUPT` SSE event and creates an
   `Interrupt` record for real-time HITL resolution. On resume, an
   `APPROVAL_RESUMED` event is emitted. See
   [Event Stream and Async Delegation](communication-events.md#interrupt--resume-protocol)
   for the full interrupt/resume protocol and `EvidencePackage` schema.
11. **Record costs**: records accumulated `TokenUsage` to `CostTracker` (if
    available), tagged with `project_id` for project-level cost aggregation.
    Cost recording failures are logged but do not affect the result.
12. **Apply post-execution transitions:**
    - `COMPLETED` termination: IN_PROGRESS -> IN_REVIEW (review gate).
      The task parks at IN_REVIEW until resolved by one of two paths:
      (a) a human approves (-> COMPLETED) or rejects (-> IN_PROGRESS
      for rework) via the approval API, or (b) the
      ``ApprovalTimeoutScheduler`` applies a configured timeout policy
      (auto-approve, auto-deny, or escalate).  Both paths delegate to
      ``ReviewGateService`` for the actual state transition.

      ``ReviewGateService`` structurally enforces no-self-review: if
      the decider equals ``task.assigned_to``, it raises
      ``SelfReviewError`` (surfaced as HTTP 403 at the approval
      controller, with a generic message that never echoes internal
      agent/task identifiers) and no transition occurs. The check
      runs in two phases: the approval controller calls
      ``check_can_decide`` as a **preflight** *before*
      ``approval_store.save_if_pending``; this guarantees a rejected
      self-review attempt never leaves a decided approval row or a
      broadcast WebSocket event behind.  ``complete_review``
      independently re-runs the check as defence-in-depth at the
      service boundary; the service makes no assumption that the
      caller ran the preflight.  ``TaskNotFoundError`` maps to 404
      and ``TaskVersionConflictError`` to 409, both with generic
      messages to avoid leaking task UUIDs via error bodies.

      The service attempts to append a ``DecisionRecord`` to the
      auditable decisions drop-box (``DecisionRepository``) for every
      completed review, capturing executor, reviewer, outcome,
      approval-ID cross-reference, and an acceptance-criteria snapshot.
      This append is **best-effort**: known transient persistence
      failures (``QueryError`` / ``DuplicateRecordError``) are logged
      via ``logger.exception`` and do NOT roll back the state
      transition (the transition is the source of truth; the drop-box
      is the audit trail).  Programming errors (``ValidationError``,
      ``TypeError``, ``AttributeError``) are deliberately NOT caught;
      they propagate loudly so schema drift surfaces in dev/CI instead
      of being masked as silent audit loss. See the "Review Gate
      Invariants" section of ``docs/design/security.md`` for the
      full three-layer enforcement model (service preflight, Pydantic
      validator, SQL CHECK constraint).

      **Identity versioning:** Agent identities
      are versioned as first-class artifacts via the generic
      ``VersioningService[T]`` infrastructure. ``ReviewGateService``
      looks up the executing agent's latest identity version and injects
      ``charter_version: {agent_id, version, content_hash}`` into the
      ``DecisionRecord.metadata`` field (best-effort; lookup failure
      is logged at WARNING and the decision record is still written).
      See [Agents](agents.md) for the full design.
    - `SHUTDOWN` termination: current status -> INTERRUPTED (or SUSPENDED
      if the checkpoint strategy successfully checkpointed the task;
      see [Graceful Shutdown](coordination.md#graceful-shutdown-protocol)).
    - `ERROR` termination: recovery strategy is applied (default
      `FailAndReassignStrategy` transitions to FAILED;
      see [Crash Recovery](coordination.md#agent-crash-recovery)).
    - `CANCELLED` termination: the task is already terminal (an operator
      cancelled or superseded it out of band), so the pipeline performs no
      re-transition and records no phantom state change. See
      [Mid-Flight Steering](mid-flight-steering.md).
    - All other termination reasons (`MAX_TURNS`, `BUDGET_EXHAUSTED`,
      `STAGNATION`, `PARKED`) leave the task in its current state.
      `STAGNATION` indicates the agent was stuck in a repetitive loop.
      `PARKED` indicates the agent paused while waiting for a human
      approval decision from `ApprovalGate`; the task remains at its
      current status (typically `IN_PROGRESS` or `AUTH_REQUIRED` in the
      task-state diagram; see [Task Lifecycle](engine.md#task-lifecycle))
      until explicitly resumed. The Approval Timeout Policy controls
      how long the parked state persists and how it ultimately
      resolves. Approval parking is distinct from the
      checkpoint-based `SUSPENDED` state produced by graceful shutdown
      (which preserves an agent's full context across a process restart);
      see [Approval Timeout Policy](security.md#approval-timeout-policy)
      and [Graceful Shutdown](coordination.md#graceful-shutdown-protocol)
      for the two parking mechanisms.
    - Each transition is synced to TaskEngine incrementally (see
      [AgentEngine <-> TaskEngine Incremental Sync](engine.md#agentengine--taskengine-incremental-sync)).
    - Transition failures are logged but do not discard the successful execution
      result.
13. **Procedural memory generation** (non-critical): when
    `ProceduralMemoryConfig` is enabled and the execution failed
    (recovery_result exists), a separate proposer LLM call analyses the
    failure and stores a `PROCEDURAL` memory entry for future retrieval.
    Optionally materialises a SKILL.md file. Failures are logged but do
    not affect the result (see [Memory Learning: Procedural Memory Auto-Generation](memory-learning.md#procedural-memory-auto-generation)).
14. **Return result**: wraps `ExecutionResult` in `AgentRunResult` with
    engine-level metadata.

**Error handling:** `MemoryError` and `RecursionError` propagate
unconditionally. `BudgetExhaustedError` (including `DailyLimitExceededError`)
returns `TerminationReason.BUDGET_EXHAUSTED` without recovery; budget
exhaustion is a controlled stop, not a crash. All other exceptions are caught
and wrapped in an `AgentRunResult` with `TerminationReason.ERROR`.

???+ note "AgentRunResult model"
    `AgentRunResult` is a frozen Pydantic model wrapping `ExecutionResult`
    with engine metadata:

    - `execution_result`: outcome from the execution loop
    - `system_prompt`: the `SystemPrompt` used for this run
    - `duration_seconds`: wall-clock run time
    - `agent_id`, `task_id`: identifiers
    - Computed fields: `termination_reason`, `total_turns`, `total_cost`,
      `is_success`, `completion_summary`

## Prompt Profiles

Auto-downgrade changes the model tier but the system prompt must adapt too.
A `PromptProfile` controls how verbose and detailed the system prompt is for
each model tier.

### Built-in Profiles

| Profile    | Tier   | Personality          | Max Personality Tokens | Org Policies | Acceptance Criteria | Autonomy |
|------------|--------|----------------------|------------------------|--------------|---------------------|----------|
| **full**   | large  | Full behavioural enums | 500                   | Included     | Nested list         | Full     |
| **standard** | medium | Description + style + traits | 200              | Included     | Nested list         | Summary  |
| **basic**  | small  | Style keyword only   | 80                     | Excluded     | Flat semicolon line | Minimal  |

### Personality Trimming

When the personality section exceeds `max_personality_tokens`, progressive
trimming enforces the budget as a secondary control after `personality_mode`:

1. **Tier 1, Drop enums**: override mode to `"condensed"` (removes behavioural
   enum fields like risk_tolerance, creativity, verbosity, etc.)
2. **Tier 2, Truncate description**: shorten `personality_description` to fit
   the remaining budget (word-boundary aware, appends `"..."`)
3. **Tier 3, Minimal fallback**: override mode to `"minimal"`
   (`communication_style` only)

Trimming metadata is attached to `SystemPrompt.personality_trim_info`
(`PersonalityTrimInfo` model with `before_tokens`, `after_tokens`,
`max_tokens`, `trim_tier`, and `budget_met` computed field). Runtime
settings in the `ENGINE` namespace control trimming
(`personality_trimming_enabled`, `personality_max_tokens_override`,
`personality_trimming_notify`).

**Dashboard notification**: when trimming activates and
`personality_trimming_notify` is enabled (default `true`), `AgentEngine`
publishes a `WsEvent(event_type=WsEventType.PERSONALITY_TRIMMED)` on the
`agents` WebSocket channel. The payload carries `agent_id`, `agent_name`,
`task_id`, `before_tokens`, `after_tokens`, `max_tokens`, `trim_tier`, and
`budget_met`. The dashboard subscribes via the global `useGlobalNotifications`
hook and renders a live toast so operators see token-budget pressure in
real time. Publishing is best-effort: failures log
`prompt.personality.notify_failed` at WARNING and never block task
execution (`MemoryError`, `RecursionError`, and `asyncio.CancelledError`
propagate per the standard best-effort publisher contract). Wiring the
notifier callback is the responsibility of the engine host; API-layer
integrations use the `synthorg.api.app.make_personality_trim_notifier`
factory to build a callback bound to the live `ChannelsPlugin`.

### Tier Flow

1. Template YAML specifies an agent's capability requirements (capability
   flags, `min_context`, optional `family`/`model_pattern`, priority)
2. Model matcher hard-filters on those requirements against each model's
   persisted `ModelMetadata`, resolves any family/pattern reference to the
   newest matching model, scores survivors, and stores the report-only
   `model_tier` (derived from the selected model's context window) in
   `ModelConfig`
3. Budget auto-downgrade updates `model_tier` when the target alias is a
   canonical tier name (`large`/`medium`/`small`); non-tier aliases (e.g.
   `"local-small"`) leave `model_tier` unchanged
4. Engine reads the preserved or updated `identity.model.model_tier` and passes
   it to `build_system_prompt()`
5. Prompt builder resolves `PromptProfile` and adapts template rendering

### Invariants

- **Authority** and **Identity** sections are **never** stripped regardless of
  profile
- When `model_tier` is `None` (unknown), the **full** profile is used as a safe
  default
- Profile selection is logged via `prompt.profile.selected` (with
  `requested_tier`, `selected_tier`, and `defaulted` flag);
  `prompt.profile.default` is emitted at DEBUG level when falling back
  to the full profile
- Personality trimming is logged via `prompt.personality.trimmed` (with
  `before_tokens`, `after_tokens`, `max_tokens`, and `trim_tier`)

## Stagnation Detection

Agents can persist in unproductive loops, repeating the same tool calls without
making progress. Stagnation detection analyses `TurnRecord` tool call history
across a sliding window, intervenes with a corrective prompt injection, and
terminates early with `STAGNATION` if correction fails.

### Protocol Interface

```python
@runtime_checkable
class StagnationDetector(Protocol):
    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> StagnationResult: ...

    def get_detector_type(self) -> str: ...
```

Async protocol; future implementations may consult external services or
LLM-based analysis.

### Default Implementation: `ToolRepetitionDetector`

Uses dual-signal detection:

1. **Repetition ratio**: excess duplicates divided by total fingerprint count
   in the window. A fingerprint appearing 3 times contributes 2 to the
   duplicate count.
2. **Cycle detection**: checks for repeating A->B->A->B patterns at the turn
   level (`seq[-2k:-k] == seq[-k:]` for cycle lengths 2..len/2).

Fingerprints are computed as `name:sha256(canonical_json_args)[:16]`,
sorted per-turn for order-independent comparison.

### Configuration (`StagnationConfig`)

| Field                  | Default | Description                                       |
|------------------------|---------|---------------------------------------------------|
| `enabled`              | `True`  | Whether stagnation detection is active             |
| `window_size`          | `5`     | Number of recent tool-bearing turns to analyse     |
| `repetition_threshold` | `0.6`   | Duplicate ratio that triggers detection            |
| `cycle_detection`      | `True`  | Whether to detect repeating patterns               |
| `max_corrections`      | `1`     | Corrective prompts before terminating (0 = none)   |
| `min_tool_turns`       | `2`     | Minimum tool-bearing turns before any check fires  |

### Intervention Flow

1. **No stagnation**: execution continues normally
2. **`INJECT_PROMPT`**: a corrective USER-role message is injected into the
   conversation (up to `max_corrections` times)
3. **`TERMINATE`**: execution terminates with `TerminationReason.STAGNATION`
   and stagnation metadata attached to the result

### Loop Integration

- **ReactLoop**: stagnation checked after each successful turn; corrections
  counter is loop-scoped
- **PlanExecuteLoop**: stagnation checked per step (different steps
  legitimately repeat similar patterns like read->edit->test); corrections
  counter is step-scoped, window resets across step boundaries
- **HybridLoop**: same per-step semantics as PlanExecuteLoop; stagnation
  checked within the mini-ReAct sub-loop, corrections counter and
  window are step-scoped
- `STAGNATION` termination leaves the task in its current state (like
  `MAX_TURNS`; the task is not failed, it's returned to the caller)

## Context Budget Management

Agents running long tasks consume their LLM context window without awareness.
The context budget system tracks fill levels, injects soft indicators into
system prompts, and compresses conversations at turn boundaries.

### Context Fill Tracking

`AgentContext` carries three context-budget fields:

- `context_fill_tokens`: estimated tokens in the full context (system prompt +
  conversation + tool definitions)
- `context_capacity_tokens`: the model's `max_context_tokens` from
  `ModelCapabilities`, or `None` when unknown
- `context_fill_percent`: computed percentage (`fill / capacity * 100`),
  `None` when capacity is unknown

Fill is re-estimated after each turn via `update_context_fill()` in
`context_budget.py`, using the `PromptTokenEstimator` protocol (default:
`DefaultTokenEstimator` at `len(text) // 4`).

### Soft Budget Indicators

`ContextBudgetIndicator` is injected into the system prompt via
`_SECTION_CONTEXT_BUDGET`:

```text
[Context: 12,450/16,000 tokens (78%) | 0 archived blocks]
```

The indicator is set at initial prompt build time. The `archived_blocks` count
is derived from `CompressionMetadata.compactions_performed`.

### Compaction Hook

`CompactionCallback` is a type alias (`Callable[[AgentContext], Coroutine[...,
AgentContext | None]]`) wired into `ReactLoop`, `PlanExecuteLoop`, and
`HybridLoop` via their constructors; the same injection pattern as `checkpoint_callback`,
`stagnation_detector`, and `approval_gate`.

The default implementation (`make_compaction_callback` in
`compaction/summarizer.py`) archives oldest conversation turns into a summary
message when `context_fill_percent` exceeds a configurable threshold (default
80%).

`CompactionConfig` controls:

| Field | Default | Description |
|-------|---------|-------------|
| `fill_threshold_percent` | `80.0` | Fill percentage that triggers compaction |
| `min_messages_to_compact` | `4` | Minimum messages before compaction is allowed |
| `preserve_recent_turns` | `3` | Recent turn pairs to keep uncompressed |

Assistant message snippets included in the summary are sanitized via
``sanitize_message()`` to redact file paths and URLs before injection into LLM
context. Compaction errors are logged but never propagated; compaction is
advisory, not critical.

### Compressed Checkpoint Recovery

`CompressionMetadata` is persisted on `AgentContext` and serialised into
checkpoint JSON. On resume, `deserialize_and_reconcile()` detects compressed
checkpoints and includes compression-aware information in the reconciliation
message:

The ``error_message`` is sanitized via ``sanitize_message()`` before inclusion to
prevent file paths and URLs from leaking into LLM context.

```text
Execution resumed from checkpoint at turn 8. Note: conversation was
previously compacted (archived 12 turns). Previous error: ...
```

### Loop Integration

- **ReactLoop**: compaction checked after stagnation detection, at turn
  boundaries (between completed turns)
- **PlanExecuteLoop**: compaction checked within step execution at turn
  boundaries, before stagnation detection
- **HybridLoop**: compaction checked at turn boundaries within the
  mini-ReAct sub-loop, same as PlanExecuteLoop

All loops use the shared `invoke_compaction()` helper from `loop_helpers.py`.

## Brain / Hands / Session

*Vocabulary adopted from the [Anthropic managed-agents engineering post](https://www.anthropic.com/engineering/managed-agents).*

The engine's architecture maps onto three decoupled planes. Each plane has a distinct responsibility, failure mode, and persistence story.

| Plane | SynthOrg Modules | Purpose |
|-------|-----------------|---------|
| **Brain** | `engine/agent_engine.py`, `AgentContext`, loop protocol (`ReactLoop`, `PlanExecuteLoop`, `HybridLoop`) | Inference loop, middleware, decision-making. Stateless between turns; all state lives in the immutable `AgentContext`. |
| **Hands** | `ToolInvoker`, `tools/sandbox/`, `SandboxCredentialManager`, `engine/_validation.py::validate_task_metadata` | Tool execution, side effects, credential scope. Credentials are stripped at the engine input boundary (task metadata validator) and at the sandbox boundary (credential manager); they never enter the brain or session planes. |
| **Session** | `observability/events/`, `engine/session.py` (`Session.replay`), checkpoint/resume | Durable event history, replay, audit. Every significant action emits a structured event; the event stream is the session's source of truth. |

### Resilience Property

The brain can fail (crash, OOM, timeout) without losing session state. Because every turn emits structured events (`execution.context.turn`, `execution.task.transition`, etc.) to the configured observability sinks, a new brain instance can reconstruct the execution context via `Session.replay(execution_id)`.

`Session.replay()` walks the event log for a given execution and reconstructs `AgentContext` (turn count, accumulated cost, task status).  It is a **best-effort** read-only reconstruction; conversation message content is not stored in events, so the replayed context has synthetic placeholder messages. The `ReplayResult.replay_completeness` field (0.0 to 1.0) indicates how much state was recovered, scored by event coverage (engine start, context creation, turn contiguity, cost data, task transitions).

This is lighter-weight than full checkpoint/resume (`checkpoint/resume.py`), which persists complete `AgentContext` snapshots and supports mid-execution suspend/resume with full message history. Use session replay for recovery after brain failure; use checkpoint/resume for deliberate pause/resume of long-running tasks.

### Credential Isolation Boundary

Credentials never enter the brain or session planes. Two enforcement points:

1. **Task metadata validator** (`engine/_validation.py::validate_task_metadata`): rejects `Task.metadata` keys matching credential patterns (token, secret, api_key, password, bearer) at the engine input boundary before execution starts.
2. **Sandbox credential manager** (`tools/sandbox/credential_manager.py`): strips credential-like environment variables before they enter sandbox containers.

See also: [Security > Credential Isolation Boundary](security.md#credential-isolation-boundary).

## ACG Vocabulary Cross-Reference

The Agentic Computation Graph (ACG) formalism (arXiv:2603.22386) provides a graph-level
vocabulary for reasoning about agentic execution: nodes as atomic computation steps, edges
as data/control flow, scheduling policies, resource constraints, and termination conditions.
SynthOrg's engine maps closely to this vocabulary. The cross-reference below is maintained
as a **bidirectional glossary**: use ACG terms when discussing execution graphs with
external audiences; use SynthOrg terms in implementation discussions.

### Vocabulary Mapping

| ACG Term | SynthOrg Equivalent | Fidelity | Notes |
|----------|--------------------|---------:|-------|
| ACG Template | `CompanyConfig` + company YAML | Partial | ACG is graph-level; SynthOrg operates at org-level |
| Realised Graph | `AgentContext` + `TaskExecution` + `CoordinationResult` | Strong | Runtime execution state |
| Execution Trace | `TurnRecord` tuple + observability events (100+ constants) | Strong | SynthOrg's trace is richer than ACG baseline |
| Nodes | LLM calls (`call_provider`), tool invocations, validation checks | Strong | Typed via `NodeType` enum on `TurnRecord.node_types` |
| Edges | `SubtaskDefinition.dependencies`, `DecompositionPlan` DAG | Strong | Multi-agent; implicit in single-agent loops |
| Scheduling Policies | `AutoLoopConfig` + `select_loop_type()` + `CoordinationConfig` | Strong | Loop selector + topology selection |
| Conditional Branching | HybridLoop replan, PlanExecuteLoop step checks | Partial | Not expressed as graph-level conditionals |
| Parallel Composition | `ParallelExecutor`, `CoordinationWave`, `asyncio.TaskGroup` | Strong | Fan-out/fan-in with DAG wave execution |
| Resource Constraints | `BudgetEnforcer`, quota degradation, `ContextBudget` | Strong | Richer than ACG: 3-layer enforcement + in-flight |
| Graph Mutation | Hybrid replanning, stagnation correction injection | Partial | Runtime; not exposed as first-class graph mutation |
| Termination Conditions | `TerminationReason` enum (7 reasons) | Strong | Explicit enumeration covers all exit paths |
| Node Cost | `TurnRecord.cost`, `TokenUsage` | Strong | Per-turn cost attribution |

**SynthOrg concepts not captured by ACG**: agent personality, episodic and procedural
memory, trust levels, autonomy presets, hiring/firing lifecycle. These are organisational
abstractions above the computation graph level.

## Agent-Controlled Context Compaction

Context compaction is invoked at turn boundaries when context fill exceeds the configured
threshold (`CompactionConfig.fill_threshold_percent`, default 80%). The `invoke_compaction()`
helper in `engine/loop_helpers.py` is shared across all three execution loops.

### Current Implementation

The current `_build_summary()` in `compaction/summarizer.py` performs simple text
concatenation: assistant message snippets capped at 100 characters each, total summary
capped at 500 characters. No LLM calls, no semantic awareness, no preservation of
reasoning artifacts.

**Known limitations**:

- Fixed 80% threshold is not context-aware; too aggressive for simple tasks, potentially
  too late for complex multi-step tasks.
- Epistemic markers ("wait", "hmm", "actually") are stripped or truncated. These carry
  disproportionate value for reasoning chains: empirical data (arXiv:2603.24472) shows
  their removal degrades accuracy by up to 63% on complex reasoning tasks (AIME24).
- No memory offloading; compacted context is discarded rather than written to
  `MemoryBackend`. LangChain's Deep Agents offload at 20k tokens; SynthOrg has no
  equivalent.
- Summarization quality is significantly below LLM-based approaches (LangChain uses
  LLM-based summarization; SynthOrg uses concatenation).

### Planned Improvements

**Phase 1 (MVP)**: Agent-controlled compaction tool + epistemic marker preservation.

- Add `compress_context` tool following the `registry_with_memory_tools()` pattern.
  Parameters: `{ strategy: "summarize"|"archive", preserve_markers: bool, reason: str }`.
- **Architecture**: Tools cannot mutate `AgentContext` (frozen Pydantic). The tool returns
  a `metadata["compaction_directive"]` flag; the loop detects it after the tool batch
  and calls `invoke_compaction()`, preserving the immutable context pattern.
- Dual-threshold safety net: 80% soft (agent-guided, system prompt indicator already
  exists) / 95% hard (system auto-compact fallback). New `CompactionConfig` fields:
  `agent_controlled: bool`, `safety_threshold_percent: float = 95.0`.
- Epistemic marker detection in `_build_summary()`: regex patterns for hesitation,
  self-correction, and uncertainty markers; messages above a density threshold are
  promoted from "archivable" to "preserved".

**Phase 2**: LLM-based summarization + memory offloading.

- Replace concatenation with a lightweight LLM summarization call
  (counted as `LLMCallCategory.SYSTEM`).
- Offload archived turns to `MemoryBackend` (episodic storage) instead of discarding.
- Task-complexity-adaptive compaction policy using `task.estimated_complexity`:
  SIMPLE = aggressive; COMPLEX/EPIC = conservative with high marker preservation.

**Phase 3**: Evaluate surprisal-based token cost (arXiv:2603.08462): per-token cost
weighted by surprisal under a frozen base model. Empirical results: 41% token reduction,
<1.5% accuracy drop. **Not recommended for Phase 1/2**: inference cost (forward pass
per token) is not justified until Phase 2 data validates the need.

If semantic token cost is needed before Phase 3, the recommended lighter proxy is
**TF-IDF importance weighting**: build a TF-IDF corpus from the current context turns,
score each token, and treat low-scoring tokens (below a tunable percentile threshold)
as compressible filler. The resulting importance map can drive selective truncation in
`_build_summary()` without any additional model inference; a significantly cheaper
approximation of the surprisal signal.

---

## See Also

- [Task & Workflow Engine](engine.md): task dispatch, routing, state coordination
- [Coordination](coordination.md): multi-agent topology, decomposition, workspace isolation
- [Verification & Quality](verification-quality.md): verification stage, review pipeline, harness middleware
- [Design Overview](index.md): full index
