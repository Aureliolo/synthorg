---
title: Coordination & Resilience
description: Agent crash recovery, graceful shutdown protocol, concurrent workspace isolation, task decomposability, coordination topology, and multi-agent pipeline.
---

# Coordination & Resilience

!!! warning "Designed behaviour; runtime in active development"

    This page is the source of truth for the **designed** behaviour of this subsystem. The multi-agent coordinator is wired at boot behind the provider-present switch: with a provider configured, `/coordinate` runs decompose, route, parallel execution, then rollup end to end; an empty company (no provider) still returns 503. The surrounding resilience features on this page (crash recovery with checkpoint resume, graceful shutdown, the self-improvement loop) remain in active development (see the [Roadmap](../roadmap/index.md)).

This page covers system-level features that span multiple agents and protect against failure: crash recovery with checkpoint resume, graceful shutdown strategies, concurrent workspace isolation (Git worktrees / virtual filesystem / per-branch), and multi-agent coordination topology (centralized, decentralized, context-dependent dispatchers).

## Agent Crash Recovery

When an agent execution fails unexpectedly (unhandled exception, OOM, process
kill), the framework applies a recovery mechanism. Recovery strategies are
implemented behind a `RecoveryStrategy` protocol, making the system pluggable.

### RecoveryStrategy Protocol

| Method | Signature | Description |
|--------|-----------|-------------|
| `recover` | `async def recover(*, task_execution, error_message, context) -> RecoveryResult` | Apply recovery to a failed task execution |
| `get_strategy_type` | `def get_strategy_type() -> str` | Return strategy type identifier (must not be empty) |

### RecoveryResult Model

| Field | Type | Description |
|-------|------|-------------|
| `task_execution` | `TaskExecution` | Updated execution after recovery (typically `FAILED`) |
| `strategy_type` | `NotBlankStr` | Strategy identifier |
| `context_snapshot` | `AgentContextSnapshot` | Redacted snapshot (turn count, accumulated cost, message count, max turns; no message contents) |
| `error_message` | `NotBlankStr` | Error that triggered recovery |
| `failure_category` | `FailureCategory` | Machine-readable classification (`TOOL_FAILURE`, `STAGNATION`, `BUDGET_EXCEEDED`, `QUALITY_GATE_FAILED`, `TIMEOUT`, `DELEGATION_FAILED`, `UNKNOWN`) |
| `failure_context` | `dict[str, Any]` | Structured strategy-specific failure metadata (deep-copied at construction; defaults to `{}`) |
| `criteria_failed` | `tuple[NotBlankStr, ...]` | Acceptance criteria that were not met (unique; validated on construction) |
| `stagnation_evidence` | `StagnationResult \| None` | Stagnation detection result when applicable |
| `checkpoint_context_json` | `str \| None` | Serialized `AgentContext` for resume (`None` for non-checkpoint strategies) |
| `resume_attempt` | `int` (ge=0) | Current resume attempt number (0 when not resuming) |
| `can_resume` | `bool` (computed) | `checkpoint_context_json is not None` |
| `can_reassign` | `bool` (computed) | `retry_count < task.max_retries` |

`failure_category` is inferred from the error message via `infer_failure_category()` (keyword-based heuristic).  `UNKNOWN` is the deliberate default when no keyword rule matches; an honest classification is more useful than a silent `TOOL_FAILURE` lie that would masquerade unknown causes in dashboards, reports, and reconciliation prompts.  Checkpoint reconciliation messages include the category and any unmet criteria (both passed through `sanitize_message` to strip paths, URLs, and prompt-injection markers) so the resumed agent has structured context about what failed without carrying leaked secrets.

**Cross-field invariants.** `RecoveryResult` enforces two cross-field rules at construction:

- `stagnation_evidence` is set iff `failure_category` is `STAGNATION` (and the evidence verdict must not be `NO_STAGNATION`; evidence that the detector ruled out stagnation cannot back a STAGNATION result).
- `criteria_failed` must be non-empty when `failure_category` is `QUALITY_GATE_FAILED`.

Strategies that only have an error string (`FailAndReassignStrategy`, `CheckpointRecoveryStrategy._build_resume_result`) use `infer_failure_category_without_evidence()`, which clamps `STAGNATION` / `QUALITY_GATE_FAILED` to `UNKNOWN`; the unclamped helper would crash construction on any error message containing the keywords "stagnation", "quality", or "criteria" because those strategies cannot supply the required sidecar data.

**Transition-reason wire format.** After a recovery, the post-execution pipeline embeds `failure_category` (and a sanitized summary of `criteria_failed` when present) into the task-status transition reason as `"Post-recovery status: <status> (failure_category=<value>[, unmet_criteria=<summary>])"`.  The `(failure_category=<value>)` suffix is a hook for downstream consumers (e.g. routing / reassignment components) to read category metadata from status history without re-parsing the raw error message.  The key name (`failure_category`) and value format are a stable contract: future consumers will depend on it, so changes require a coordinated rollout.

**State-transition log timing.** Per CLAUDE.md, every persisted status hop emits an INFO-level `*_STATUS_TRANSITIONED` event (`WORKFLOW_EXEC_STATUS_TRANSITIONED`, `WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED`) **after** persistence succeeds. A save failure raises before the log fires, so the audit trail only ever records transitions that actually landed; this avoids the "phantom transition" failure mode where a `VersionConflictError` would otherwise leave a log entry showing a hop that the database never accepted. The bootstrap `PENDING -> RUNNING` state is set inline during initial execution creation in `WorkflowExecutionService` rather than as a persisted transition, so no separate event fires for that hop; the persisted hops covered by the transition log today are the three terminal ones (`RUNNING -> COMPLETED` / `-> FAILED` / `-> CANCELLED`). Subsystems that also emit a terminal-state event (`WORKFLOW_EXEC_COMPLETED`, `WORKFLOW_EXEC_FAILED`, `WORKFLOW_EXEC_CANCELLED`) keep those for final-hop summaries; the transition-log event is the cross-hop audit-stream entry carrying `from_status` / `to_status` / identifiers.

### Recovery Strategies

=== "Strategy 1: Fail-and-Reassign"

    **Default / MVP**

    The engine catches the failure at its outermost boundary, logs a redacted
    `AgentContext` snapshot (turn count, accumulated cost; excluding message
    contents to avoid leaking sensitive prompts/tool outputs), transitions the
    task to `FAILED`, and makes it available for reassignment (manual or
    automatic via the task router).

    ```yaml
    crash_recovery:
      strategy: "fail_reassign"            # fail_reassign, checkpoint
    ```

    - Simple, no persistence dependency
    - All progress is lost on crash; acceptable for short single-agent tasks

    On crash:

    1. Catch exception at the `AgentEngine` boundary (outermost `try/except`
       in `AgentEngine.run()`)
    2. Log at ERROR with redacted `AgentContextSnapshot` (turn count,
       accumulated cost, message count, max turns; message contents excluded)
    3. Transition `TaskExecution` -> `FAILED` with the exception as the failure
       reason
    4. `RecoveryResult.can_reassign` reports whether `retry_count < max_retries`

    !!! info
        The `can_reassign` flag is computed and returned in `RecoveryResult`.
        The caller (task router) is responsible for incrementing `retry_count`
        when creating the next `TaskExecution`.

=== "Strategy 2: Checkpoint Recovery"

    The engine persists an `AgentContext` snapshot after each completed turn. On
    crash, the framework detects the failure (via heartbeat timeout or
    exception), loads the last checkpoint, and resumes execution from the exact
    turn where it left off. The immutable `model_copy(update=...)` pattern makes
    checkpointing trivial; each `AgentContext` is a complete, self-contained
    frozen state that serializes cleanly via `model_dump_json()`.

    ```yaml
    crash_recovery:
      strategy: "checkpoint"
      checkpoint:
        persist_every_n_turns: 1           # checkpoint frequency
        # Storage backend determined by the injected CheckpointRepository
        heartbeat_interval_seconds: 30     # detect unresponsive agents
        max_resume_attempts: 2             # retry limit before falling back to fail_reassign
    ```

    - Preserves progress; critical for long tasks (multi-step plans,
      epic-level work)
    - Requires persistence layer and reconciliation message on resume
    - Natural fit with the existing immutable state model

    When resuming from a checkpoint, the agent receives a system message
    informing it of the resume point (turn number) and the error that triggered
    recovery. This reconciliation message allows the agent to review its
    progress and adapt. Richer reconciliation (e.g. workspace change
    detection) is planned.

=== "Lightweight Alternative: Session Replay"

    `Session.replay()` (`engine/session.py`) provides a lighter-weight
    alternative to full checkpoint/resume.  It reconstructs `AgentContext`
    from the observability event log rather than from a persisted checkpoint
    snapshot.

    - **Read-only reconstruction**: replays turn count, accumulated cost,
      and task status transitions, but not full conversation history
      (events do not store message content; turns are represented as
      placeholder messages).
    - **No persistence dependency**: relies on whichever observability sink
      the operator configured (structlog file, OTLP backend, Postgres).
    - **Best-effort**: `ReplayResult.replay_completeness` (0.0 to 1.0) indicates
      how much state was recovered.
    - **Use case**: recovery after brain failure when checkpoint persistence
      is not configured or the checkpoint is stale.

    See [Brain / Hands / Session](agent-execution.md#brain--hands--session) for the full
    architecture.

## Graceful Shutdown Protocol

When the process receives SIGTERM/SIGINT (user Ctrl+C, Docker stop, systemd
shutdown), the framework stops cleanly without losing work or leaking costs.
Shutdown strategies are implemented behind a `ShutdownStrategy` protocol.

Shutdown-time `SUSPENDED` is distinct from the in-process `PARKED` state used
when an agent waits for human approval; see
[Approval Timeout Policy](security.md#approval-timeout-policy) for the
agent-driven parking mechanism.

### Strategy 1: Cooperative with Timeout (Default / MVP)

The engine sets a shutdown event, stops accepting new tasks, and gives in-flight
agents a grace period to finish their current turn. Agents check the shutdown
event at turn boundaries (between LLM calls, before tool invocations) and exit
cooperatively. After the grace period, remaining agents are force-cancelled.
**All tasks terminated by this strategy (whether they exited cooperatively or
were force-cancelled) are marked `INTERRUPTED`** by the engine layer.
(Strategy 4 uses `SUSPENDED` for successfully checkpointed tasks instead;
see [Strategy 4](#strategy-4-checkpoint-and-stop).)

```yaml
graceful_shutdown:
  strategy: "cooperative_timeout"    # cooperative_timeout, immediate, finish_tool, checkpoint
  grace_seconds: 30                  # time for agents to finish cooperatively
  cleanup_seconds: 5                 # time for final cleanup (persist cost records, close connections)
```

On shutdown signal:

1. Set `shutdown_event` (`asyncio.Event`); agents check this at turn
   boundaries
2. Stop accepting new tasks (drain gate closes)
3. Wait up to `grace_seconds` for agents to exit cooperatively
4. Force-cancel remaining agents (`task.cancel()`); tasks transition to
   `INTERRUPTED`
5. Cleanup phase (`cleanup_seconds`): persist cost records, close provider
   connections, flush logs

!!! info "INTERRUPTED status"
    `INTERRUPTED` indicates the task was stopped due to process shutdown
    (regardless of whether the agent exited cooperatively or was force-cancelled)
    and is eligible for manual or automatic reassignment on restart. Valid
    transitions: `ASSIGNED -> INTERRUPTED`, `IN_PROGRESS -> INTERRUPTED`,
    `INTERRUPTED -> ASSIGNED`.

!!! tip "Cross-platform compatibility"
    `loop.add_signal_handler()` is not supported on Windows. The implementation
    uses `signal.signal()` as a fallback. SIGINT (Ctrl+C) works cross-platform;
    SIGTERM on Windows requires `os.kill()`.

!!! warning "In-flight LLM cost leakage"
    Non-streaming API calls that are interrupted result in tokens billed but no
    response received (silent cost leak). The engine logs request start (with
    input token count) before each provider call, so interrupted calls have at
    minimum an input-cost audit record. Streaming calls are charged only for
    tokens sent before disconnect.

### Strategy 2: Immediate Cancel

All agent tasks are cancelled immediately via `task.cancel()` with no grace
period. Fastest shutdown but highest data loss; partial tool side effects,
billed-but-lost LLM responses. Tasks are marked `INTERRUPTED`.

```yaml
graceful_shutdown:
  strategy: "immediate"
  cleanup_seconds: 5
```

### Strategy 3: Finish Current Tool

Like cooperative timeout, but uses a per-tool timeout (default 60s) to allow
the current tool invocation to complete. The execution loop finishes the
current tool before checking shutdown at turn boundaries; this strategy
gives a longer window for that. Tasks that exceed the tool timeout are
force-cancelled and marked `INTERRUPTED`.

```yaml
graceful_shutdown:
  strategy: "finish_tool"
  tool_timeout_seconds: 60
  cleanup_seconds: 5
```

### Strategy 4: Checkpoint and Stop

On shutdown signal, agents checkpoint cooperatively during the grace period.
Stragglers are checkpointed via a `checkpoint_saver` callback, then cancelled.
Successfully checkpointed tasks transition to `SUSPENDED` (not `INTERRUPTED`);
failed checkpoints fall back to `INTERRUPTED`. On restart, the engine loads
checkpoints and resumes execution from the exact point of interruption. This
naturally extends [Checkpoint Recovery](#agent-crash-recovery); the only
difference is whether the checkpoint was written proactively (graceful
shutdown) or loaded from the last turn (crash recovery).

!!! info "SUSPENDED vs INTERRUPTED"
    `SUSPENDED` indicates the task was checkpointed before stop and can resume
    from the exact point of interruption.  `INTERRUPTED` indicates the task was
    stopped without a checkpoint and requires full reassignment.  Both are
    non-terminal: `SUSPENDED -> ASSIGNED`, `INTERRUPTED -> ASSIGNED`.

```yaml
graceful_shutdown:
  strategy: "checkpoint"
  grace_seconds: 30
  cleanup_seconds: 5
```

## Concurrent Workspace Isolation

When multiple agents work on the same codebase concurrently, they may need to
edit overlapping files. The framework provides a pluggable
`WorkspaceIsolationStrategy` protocol for managing concurrent file access.

### Strategy 1: Planner + Git Worktrees (Default)

The task planner decomposes work to minimize file overlap across agents. Each
agent operates in its own git worktree (shared `.git` object database,
independent working tree). On completion, branches are merged sequentially.

This is the dominant industry pattern (used by major coding agent products
and IDE background agents).

```mermaid
flowchart TD
    P[Planner decomposes task]
    P --> A[Agent A: src/auth/ worktree-A]
    P --> B[Agent B: src/api/ worktree-B]
    P --> C[Agent C: tests/ worktree-C]
    A --> M[Sequential merge]
    B --> M
    C --> M
    M --> T[Textual conflicts: escalate to human or review agent]
    M --> S[Semantic conflicts: review agent evaluates merged result]
```

???+ example "Workspace isolation configuration"

    ```yaml
    workspace_isolation:
      strategy: "planner_worktrees"      # planner_worktrees, sequential, file_locking
      planner_worktrees:
        max_concurrent_worktrees: 8
        merge_order: "completion"        # completion (first done merges first), priority, manual
        conflict_escalation: "human"     # human, review_agent
        cleanup_on_merge: true
        semantic_analysis:
          enabled: false
          file_extensions: [".py"]
          max_files: 50
          max_file_bytes: 524288
          git_concurrency: 10
          llm_model: null
          llm_temperature: 0.1
          llm_max_tokens: 4096
          llm_max_retries: 2
    ```

- True filesystem isolation; agents cannot overwrite each other's work
- Maximum parallelism during execution; conflicts deferred to merge time
- Leverages mature git infrastructure for merge, diff, and history

### Semantic Conflict Detection

Git merges catch textual conflicts (overlapping edits to the same lines), but
many real-world integration bugs are *semantic* - the merge succeeds textually
yet the combined code is broken. The semantic conflict detection subsystem
analyzes merged results to catch these issues before they reach main.

**SemanticAnalyzer protocol and composite pattern.** The `SemanticAnalyzer`
protocol defines a single `analyze(workspace, changed_files, repo_root, base_sources)` method.
The default `CompositeSemanticAnalyzer` dispatches all configured analyzers
concurrently via `asyncio.TaskGroup` and deduplicates their combined results,
allowing AST-based checks and optional LLM-based analysis to compose
transparently. Analyzer failures are logged and skipped without aborting
the remaining analyzers.

**AST-based checks.** Four pure-function checks run against the merged source
without external dependencies:

1. **Removed references** - detects calls or imports referencing names that no
   longer exist in the merged code (e.g., Agent A renames a function, Agent B
   calls the old name).
2. **Signature mismatches** - detects functions whose signatures changed between
   base and merged versions in ways that break existing call sites.
3. **Duplicate definitions** - detects multiple top-level definitions of the
   same name in a single file (e.g., two agents independently add a `process()`
   function that git merges into disjoint hunks).
4. **Import conflicts** - detects conflicting imports of the same name from
   different modules.

**Optional LLM-based analysis.** When `llm_model` is configured in
`SemanticAnalysisConfig`, a provider-backed analyzer sends the diff to an LLM
for deeper reasoning about subtle semantic issues that AST checks cannot catch.

**SemanticAnalysisConfig.** A frozen Pydantic model controlling the analysis
pipeline: `enabled` toggle, `file_extensions` filter, `max_files` and
`max_file_bytes` limits to bound analysis cost, `git_concurrency` to cap
concurrent `git show` subprocess fan-out, and LLM-specific settings
(`llm_model`, `llm_temperature`, `llm_max_tokens`, `llm_max_retries`).

**Flow through MergeResult and MergeOrchestrator.** After a textually
successful merge, the `MergeOrchestrator` invokes the configured
`SemanticAnalyzer`. Any detected issues are attached to the `MergeResult` as
`semantic_conflicts` (tuple of `MergeConflict` with `conflict_type=SEMANTIC`).
The calling code can then decide whether to accept, revert, or escalate based
on the severity and count of semantic conflicts.

### Future Strategies

Strategy 2: Sequential Dependencies
:   Tasks with overlapping file scopes are ordered sequentially via a dependency
    graph. Prevents conflicts by construction but limits parallelism. Requires
    upfront knowledge of which files a task will touch.

Strategy 3: File-Level Locking
:   Files are locked at task assignment time. Eliminates conflicts at the source
    but requires predicting file access, difficult for LLM agents that
    discover what to edit as they go. Risk of deadlock if multiple agents need
    overlapping file sets.

### State Coordination vs Workspace Isolation

These are complementary systems handling different types of shared state:

| State Type | Coordination | Mechanism |
|-----------|-------------|-----------|
| Framework state (tasks, assignments, budget) | Centralized single-writer (`TaskEngine`) | `model_validate` / `with_transition` via async queue |
| Code and files (agent work output) | Workspace isolation (`WorkspaceIsolationStrategy`) | Git worktrees / branches |
| Agent memory (personal) | Per-agent ownership | Each agent owns its memory exclusively |
| Org memory (shared knowledge) | Single-writer (`OrgMemoryBackend`) | `OrgMemoryBackend` protocol with role-based write access control |

### Worktree Disk Quota

Per-worktree disk usage limits with a background watcher that emits warning
and exceeded events when thresholds are crossed.

**Configuration** (on `PlannerWorktreesConfig`):

| Field | Default | Description |
|-------|---------|-------------|
| `max_disk_gb_per_worktree` | `5.0` | Maximum disk usage in GB per worktree |
| `auto_cleanup_on_threshold` | `True` | Signal cleanup when limit exceeded |
| `cleanup_warning_threshold` | `0.8` | Usage ratio for warning events (0.5-1.0) |

**Watcher** (`DiskQuotaWatcher`): checks worktree disk usage via recursive
directory size computation. Emits `WORKSPACE_DISK_WARNING` at the warning
threshold and `WORKSPACE_DISK_EXCEEDED` at the limit. Does not delete
worktrees directly; signals the `WorkspaceManager` to act.

**Module**: `src/synthorg/engine/workspace/disk_quota.py`

### Persistent Per-Project Workspace and Push Queue Serialisation

Each project gets a 1:1 persistent git-backed working tree on the
runtime volume. `ProjectWorkspaceService.get_or_provision(project_id)`
materialises the working tree under
`<base_root>/projects/<project_id>/` on first touch via the configured
`GitBackend` (`embedded` default; `local_path` / `external_remote` are
opt-in via config). The tree survives across agents, tasks, and
sessions. `GitBackendConfig.kind` is authoritative: a persisted row
whose kind differs from the live config triggers a re-provision under
the new backend and a `WORKSPACE_BACKEND_KIND_CHANGED` log event.

When N agents finish concurrently on one project, their
merge-to-default-branch + push-to-backend operations route through a
per-project FIFO serial queue (`PushQueueCoordinator`) so concurrent
pushes never collide at the git backend. The queue sits in front of
the `WorkspaceIsolationStrategy` seam, so a future virtual-branch
strategy supplies its own `merge_workspace` without changing the
queue. A conflicted merge resolves the caller future without pushing
(the queue refuses to push a broken default branch). `stop()` drains
in flight then exits cleanly; `WorkspacePushError` distinguishes a
forge-rejection push failure from a local `WorkspaceMergeError`.

Events emitted: `PROJECT_WORKSPACE_PROVISIONED`,
`PROJECT_WORKSPACE_REUSED`, `WORKSPACE_BACKEND_KIND_CHANGED`,
`WORKSPACE_PUSH_QUEUE_ENQUEUED`, `WORKSPACE_PUSH_QUEUE_MERGED`,
`WORKSPACE_PUSH_QUEUE_FAILED`, `WORKSPACE_PUSH_QUEUE_WORKER_FAILED`.

**Modules**:
- `src/synthorg/engine/workspace/project_workspace_service.py`
- `src/synthorg/engine/workspace/git_backend/` (protocol + 3 strategies + factory)
- `src/synthorg/engine/workspace/push_queue.py`
- `src/synthorg/engine/workspace/service.py` (per-project queue cache + `merge_workspace_with_push`)

### Reproducible per-project environments

Orthogonal to the concurrent workspace isolation above (which arbitrates
simultaneous agent edits to one codebase), this provisions a reproducible
dev environment from committed declarations. Each project declares its dev
environment in committed files so "worked in the agent sandbox" equals
"works on a fresh clone". `EnvironmentService.
get_or_provision(project_id, ...)` resolves the declaration via a
config-selected `EnvironmentStrategy` (`manifest` default; `devcontainer`
and `nix` opt-in), scaffolds a default declaration into a fresh workspace
when absent (`auto_seed`), commits it through `GitWorkspaceCommitter`, and
provisions it once per `(project_id, declaration_hash)` (the persisted
`project_environments` row is the durable cache; a declaration edit
invalidates it). Provisioning is fail-loud: a broken environment never
presents itself as ready.

The bootstrap manifest (`synthorg.env.yaml`) runs its ordered setup
commands into the mounted workspace through whichever sandbox backend the
build/test tool categories resolve to, and emits a stock `bootstrap.sh` so
a fresh clone reproduces with no SynthOrg present; the devcontainer
strategy builds a sealed image (Docker backend only). The provisioned
result threads to the agent's per-tool-call sandbox via the ambient
`ActiveSandboxEnvironment` contextvar (image override + env additions),
bound for the scope of one agent run in the worker execution path. The
override image runs under the existing hardened sandbox host config
(read-only root, `CapDrop: ALL`, `no-new-privileges`).

A transient image build failure (timeout, registry/network hiccup) is
retried with exponential backoff via `GeneralRetryHandler`; a deterministic
build failure (bad Dockerfile) is not. Declaration-sourced env additions
are screened through the sandbox denylist (a declared secret or
exec-hijacking var is dropped), unlike the trusted internal hardening
overrides.

Events emitted: `ENVIRONMENT_PROVISION_START`, `ENVIRONMENT_PROVISIONED`,
`ENVIRONMENT_PROVISION_FAILED`, `ENVIRONMENT_PROVISION_SKIPPED`,
`ENVIRONMENT_REUSED`, `ENVIRONMENT_KIND_CHANGED`,
`ENVIRONMENT_DECLARATION_SCAFFOLDED`, `ENVIRONMENT_ROW_PERSISTED`,
`ENVIRONMENT_LOCKFILE_PATH_REJECTED`, `ENVIRONMENT_IMAGE_BUILD_START`,
`ENVIRONMENT_IMAGE_BUILD_COMPLETE`, `ENVIRONMENT_IMAGE_BUILD_FAILED`,
`ENVIRONMENT_IMAGE_BUILD_RETRY`.

**Modules**:
- `src/synthorg/engine/workspace/environment/` (protocol + 3 strategies +
  factory + service + committer + hash cache + image builder + templates)
- `src/synthorg/tools/sandbox/active_environment.py` (ambient contextvar)
- `src/synthorg/workers/environment_runner.py` (sandbox-backed runner)

## Task Decomposability & Coordination Topology

Empirical research on agent scaling
([Kim et al., 2025](https://arxiv.org/abs/2512.08296); 180 controlled
experiments across 3 LLM families and 4 benchmarks) demonstrates that **task
decomposability is the strongest predictor of multi-agent effectiveness**,
stronger than team size, model capability, or coordination architecture.

### Task Structure Classification

Each task carries a `task_structure` field classifying its decomposability:

| Structure | Description | Multi-Agent Effect | Example |
|-----------|-------------|------------|---------|
| `sequential` | Steps must execute in strict order; each depends on prior state | **Negative** (-39% to -70%) | Multi-step build processes, ordered migrations, chained API calls |
| `parallel` | Sub-problems can be investigated independently, then synthesized | **Positive** (+57% to +81%) | Financial analysis (revenue + cost + market), multi-file review, research across sources |
| `mixed` | Some sub-tasks are parallel, but a sequential backbone connects phases | **Variable** (depends on ratio) | Feature implementation (design // research -> implement -> test) |

Classification can be:

- **Explicit**: set in task config by the task creator or manager agent
- **Inferred**: derived from task properties (tool count, dependency graph,
  acceptance criteria structure) by the task router

### Per-Task Coordination Topology

The [communication pattern](communication.md#communication-patterns) is
configured at the company level, but **coordination topology can be selected
per-task** based on task structure and properties.

| Task Properties | Recommended Topology | Rationale |
|----------------|---------------------|-----------|
| `sequential` + few artifacts (<=4) | **Single-agent (SAS)** | Coordination overhead fragments reasoning capacity on sequential tasks |
| `parallel` + structured domain | **Centralized** | Orchestrator decomposes, sub-agents execute in parallel, orchestrator synthesizes. Lowest error amplification (4.4x) |
| `parallel` + exploratory/open-ended | **Decentralized** | Peer debate enables diverse exploration of high-entropy search spaces |
| `mixed` | **Context-dependent** | Sequential backbone handled by single agent; parallel sub-tasks delegated to sub-agents |

### Auto Topology Selector

When topology is set to `"auto"`, the engine selects coordination topology based
on measurable task properties:

```yaml
coordination:
  topology: "auto"                    # auto, sas, centralized, decentralized, context_dependent
  auto_topology_rules:
    sequential_override: "sas"
    parallel_default: "centralized"
    mixed_default: "context_dependent"
  max_concurrency_per_wave: null        # None = unlimited
  max_delegation_rounds: 3             # soft cap; hard abort at 2x (6)
  fail_fast: false
  enable_workspace_isolation: true
  base_branch: main
```

The auto-selector uses task structure, artifact count, and (when available from
the memory subsystem) historical single-agent success rate as inputs. Kim et al.
achieved 87% accuracy predicting optimal architecture from task properties
across held-out configurations.

### Coordination Group Size Bounds

Per-task coordination-group size is **not** the same as per-company size. An
Enterprise Org template with 20-50 agents does not run 20-50-agent coordination
waves; it runs small coordination groups drawn from the roster.

| Scope | Bound | Enforcement |
|-------|-------|-------------|
| Per-coordination-group (agents in a single `coordination_topology` wave) | **3-4 agents** (recommended) | `CoordinationConfig.max_concurrency_per_wave` |
| Per-task total team (orchestrator + sub-agents + verifiers) | **~7 agents** | Soft cap; logged warning above threshold |
| Per-meeting participants | **3-5 ideal, 8 hard cap** | Enforced by meeting protocol token budgets and quadratic-growth warnings (see [Meeting Protocol](communication-coordination.md#meeting-protocol)) |
| Per-company / org roster | **No hard bound** | Organizational-simulation fidelity, not per-task reasoning efficiency |

### Multi-Agent Coordination Pipeline

The `MultiAgentCoordinator` orchestrates the end-to-end pipeline that transforms
a parent task into parallel agent work:

```text
decompose -> route -> resolve topology -> validate -> dispatch -> rollup -> update parent
```

**Pipeline phases:**

1. **Decompose**: `DecompositionService` breaks the parent task into subtasks
   with a dependency DAG. The LLM-backed decomposer routes task title,
   description, and acceptance criteria through `wrap_untrusted(TAG_TASK_DATA, ...)`
   before interpolating them into the prompt; the system prompt appends the
   canonical `untrusted_content_directive` so the model is told the fenced
   content is untrusted input. See [SEC-1: Prompt Safety](../reference/sec-prompt-safety.md).
2. **Route**: `TaskRoutingService` assigns each subtask to an agent based on
   skills, workload, and topology
3. **Resolve topology**: reads topology from routing decisions; falls back to
   `CENTRALIZED` if `AUTO` was not resolved upstream
4. **Validate**: fails the pipeline if all subtasks are unroutable
5. **Dispatch**: a `TopologyDispatcher` executes waves (workspace setup ->
   parallel execution -> merge -> teardown)
6. **Rollup**: aggregates subtask statuses into a `SubtaskStatusRollup`
7. **Update parent**: transitions the parent task via `TaskEngine` (if provided)

Each phase produces a `CoordinationPhaseResult` (success/failure + duration).

**Topology dispatchers:**

| Dispatcher | Topology | Workspace Isolation | Wave Strategy |
|-----------|----------|-------------------|---------------|
| `SasDispatcher` | SAS | Never | Sequential waves from DAG |
| `CentralizedDispatcher` | Centralized | Optional (config-driven) | DAG waves, post-execution merge |
| `DecentralizedDispatcher` | Decentralized | Mandatory (raises if unavailable) | DAG waves, post-execution merge |
| `ContextDependentDispatcher` | Context-dependent | Per-wave (multi-subtask waves only) | DAG waves, per-wave merge/teardown |

The `select_dispatcher` factory maps a resolved `CoordinationTopology` to the
appropriate dispatcher; `AUTO` must be resolved before dispatch.

#### Per-Agent Attribution

After the pipeline completes, `build_agent_contributions()` in
`coordination/attribution.py` produces a `tuple[AgentContribution, ...]` from
routing decisions and wave outcomes:

- **`AgentContribution`**: frozen Pydantic model recording each agent's
  `contribution_score` (0.0 to 1.0), `failure_attribution` classification, and
  optional `evidence` excerpt.
- **Failure attribution categories**: `"direct"` (agent's own failure),
  `"upstream_contamination"` (bad input from another agent),
  `"coordination_overhead"` (system-initiated: budget, shutdown, parking),
  `"quality_gate"` (failed quality check).
- **Integration**: contributions are fed into `PerformanceTracker
  .record_coordination_contributions()` for trend analysis.

---

## Coordination Service Layer

MCP tools for coordination and ceremony policy route through dedicated service facades instead of reaching into the coordinator + scheduler directly, so the handler layer stays thin and audit logging + pagination stay uniform across every read.

| Service | Module | Role |
|---|---|---|
| `CoordinationService` | `src/synthorg/coordination/service.py` | Read-only facade over `coordination_metrics_store`. Powers `synthorg_coordination_get_task_metrics` (newest-first lookup for a given `task_id`, or `None` mapped to a `not_found` envelope) and `synthorg_coordination_metrics_list` (paged metrics with `(items, total)` return shape). Triggering coordination is intentionally out of scope on the MCP surface; callers trigger runs over REST (`POST /tasks/{task_id}/coordinate`) and inspect the resulting metrics via MCP. |
| `ScalingDecisionService` | `src/synthorg/hr/scaling/decision_service.py` | Read-only facade over the scaling decision history, scaling config, and the manual-trigger entry point. Powers `synthorg_scaling_list_decisions`, `synthorg_scaling_get_decision`, `synthorg_scaling_get_config`, and `synthorg_scaling_trigger`. `trigger` is the one write-shaped tool in the group and records an audit event with the resolved actor + reason. |
| `CeremonyPolicyService` | `src/synthorg/coordination/ceremony_policy/service.py` | Glue between MCP handlers and the ceremony policy helpers in `api/controllers/ceremony_policy.py` + `engine/workflow/ceremony_policy.py`. Exposes `get_config`, `get_resolved`, and `get_active_strategy`. Returns a frozen `ActiveCeremonyStrategy` model that enforces the `strategy`/`sprint_id` coupling invariant via a cross-field validator: both fields are always either both set (a sprint is active and locked to a strategy) or both `None`. |

The services import `AppState` for re-use of the existing 3-level resolution (`settings_service` + `config_resolver` + `ceremony_scheduler`) rather than introducing a parallel protocol stack.

## See Also

- [Task & Workflow Engine](engine.md): task dispatch, state coordination
- [Agent Execution](agent-execution.md): per-agent execution loop, prompt profiles
- [Verification & Quality](verification-quality.md): review pipeline, verification stage
- [Design Overview](index.md): full index
