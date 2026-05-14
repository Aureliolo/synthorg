---
title: "Evaluating the Agentic Computation Graph (ACG) Formalism for SynthOrg Engine Vocabulary, Structural Credit Assignment, and Agent Pruning"
issue: 848
source: "https://huggingface.co/papers/2603.22386"
companion: "https://github.com/IBM/awesome-agentic-workflow-optimization"
date: 2026-04-07
related: [690]
---

# ACG Formalism Evaluation for SynthOrg Engine Architecture

## Context

The IBM/RPI survey "From Static Templates to Dynamic Runtime Graphs" (arXiv:2603.22386)
introduces the Agentic Computation Graph (ACG) formalism to unify the vocabulary for
describing agent workflows: from static templates to dynamic runtime graphs, scheduling
policies, execution traces, and mutation strategies. SynthOrg has all these concepts but
expresses them through domain-specific names (execution loops, hybrid plans, turn records,
coordination topology). This evaluation assesses whether adopting ACG vocabulary would
improve architecture clarity, identify gaps, and inform design decisions for structural
credit assignment and agent pruning.

---

## ACG Vocabulary Mapping

The following table maps each ACG concept to its SynthOrg equivalent, with fidelity
assessment and source file references.

### Core Graph Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| **ACG Template** | `CompanyConfig` + Company YAML | `src/synthorg/core/company.py`, `src/synthorg/config/schema.py` | Partial | ACG templates are graph-level (workflow topology). SynthOrg's YAML is org-level (agent roster, tool permissions, budget). Closer analogue would be `WorkflowDefinition` for workflow templates. |
| **Realized Graph** | `AgentContext` + `TaskExecution` + `CoordinationResult` | `src/synthorg/engine/context.py`, `src/synthorg/engine/coordination/models.py` | Strong | The realized graph IS the running state: context, history, accumulated cost, current position. Multi-agent coordination adds `CoordinationPhaseResult` per phase. |
| **Execution Trace** | `tuple[TurnRecord, ...]` in `ExecutionResult` + observability events | `src/synthorg/engine/loop_protocol.py`, `src/synthorg/observability/events/` | Strong | SynthOrg's trace is richer than ACG baseline: per-turn cost, token usage, tool fingerprints, stagnation signals, quality scores. 100+ event constant domains. |
| **Nodes (atomic actions)** | LLM calls (`call_provider`), tool invocations (`execute_tool_calls`), validation gates (`check_budget`, `check_stagnation`) | `src/synthorg/engine/loop_helpers.py` | Partial | Node typing is implicit in loop control flow, not a first-class abstraction. There is no `Node` type; actions are identified by function names and turn records. |
| **Edges (control/data flow)** | `SubtaskDefinition.dependencies` DAG, `DecompositionPlan.dependency_edges` | `src/synthorg/engine/decomposition/models.py` | Strong for multi-agent | Edges are explicit in multi-agent decomposition (dependency DAG). Implicit in single-agent loops (sequential execution order, no formal edge representation). |
| **Scheduling Policies** | `AutoLoopConfig` + `select_loop_type()` + `CoordinationConfig` + `AutoTopologyConfig` | `src/synthorg/engine/loop_selector.py`, `src/synthorg/engine/routing/models.py` | Strong | Three-way loop selection (react/plan-execute/hybrid) and topology selection (SAS/centralized/decentralized/context-dependent) are scheduling policies. Budget-aware downgrade is a resource-constrained policy. |

### Dynamic Behavior Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| **Conditional branching** | HybridLoop replan decisions, PlanExecuteLoop step completion | `src/synthorg/engine/hybrid_loop.py`, `src/synthorg/engine/hybrid/step_helpers.py`, `src/synthorg/engine/hybrid/replan_helpers.py` | Partial | Branching is embedded in loop logic (replan if step fails), not graph-level conditional edges. No formal "if node X succeeds, take edge Y" representation. |
| **Parallel composition** | `ParallelExecutor`, `CoordinationWave`, `asyncio.TaskGroup` | `src/synthorg/engine/parallel.py`, `src/synthorg/engine/coordination/models.py` | Strong | Parallel waves in coordination are first-class. `ParallelExecutor` handles concurrent subtask dispatch with `fail_fast` semantics. |
| **Graph mutation** | Hybrid replanning (`attempt_replan`), stagnation correction injection | `src/synthorg/engine/hybrid/replan_helpers.py`, `src/synthorg/engine/stagnation/` | Partial | Replanning mutates the execution plan (new subtask list). Stagnation correction injects a new message. These are graph mutations but are not described in those terms. |
| **Termination conditions** | `TerminationReason` enum (7 values: COMPLETED, MAX_TURNS, BUDGET_EXHAUSTED, SHUTDOWN, PARKED, STAGNATION, ERROR) | `src/synthorg/engine/loop_protocol.py` | Strong | Richer than typical ACG termination models. 7 named reasons provide precise signal for recovery and routing decisions. |

### Resource and Cost Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| **Node cost** | `TurnRecord.cost` per turn, `TokenUsage` per completion | `src/synthorg/engine/loop_protocol.py`, `src/synthorg/providers/models.py` | Strong | Per-turn cost tracking with provider breakdown. Accumulated over execution via `ctx.accumulated_cost`. |
| **Resource constraints** | `BudgetEnforcer` (3-layer), quota degradation, context budget | `src/synthorg/budget/enforcer.py`, `src/synthorg/engine/context_budget.py` | Strong (exceeds ACG) | SynthOrg's resource model is more sophisticated than ACG: multi-layer enforcement, per-agent daily limits, context fill tracking, risk budget. |
| **Quality-cost tradeoffs** | Budget-aware loop downgrade (hybrid->plan_execute at 80%), model auto-downgrade, quota degradation strategies | `src/synthorg/engine/loop_selector.py`, `src/synthorg/budget/enforcer.py` | Strong | Explicit tradeoff mechanisms with hard budget caps. Downgrade-only at task boundaries (consistency guarantee). |

### Concepts SynthOrg Has That ACG Does Not Capture

- **Progressive trust**: agent trust levels (RESTRICTED/STANDARD/ELEVATED) with human
  approval for promotion. No ACG equivalent.
- **Personality and behavioral configuration**: `PersonalityConfig` with Big Five + behavioral
  enums affecting decision style. No ACG equivalent.
- **Memory injection**: episodic and procedural memory retrieval shaping context before
  execution. No ACG equivalent.
- **Prompt profiles**: verbosity adaptation by model tier. No ACG equivalent.
- **Autonomy levels**: 4 presets (full/semi/supervised/locked) with tool permission gating.
  No ACG equivalent.

---

## ACG Survey Findings Validation

The survey identifies four structural findings that apply to SynthOrg's architecture:

### Finding 1: Structural Improvements > Prompt Refinement When Scaffold Is Poorly Matched

**Claim**: When an agent workflow uses the wrong graph structure (e.g., sequential when
parallel would be correct), prompt engineering cannot compensate. Structural changes yield
greater gains.

**SynthOrg validation**: Confirmed by the loop selector. The auto-selector maps task
complexity to loop type (simple->react, medium->plan_execute, complex->hybrid). The
documentation for this design explicitly states that choosing the wrong loop for a task
complexity degrades quality beyond what prompt tuning can recover. The budget-aware downgrade
(hybrid->plan_execute at 80% monthly) is a deliberate quality tradeoff, accepted because
the budget constraint makes the simpler structure correct.

**Implication**: The loop selector is doing real structural work. Adding complexity to
system prompts for tasks that should use a different loop is not a substitute. This
validates investing in the auto-selector's classification accuracy over prompt length.

### Finding 2: Strong Verifiers Enable More Aggressive Graph Mutation

**Claim**: Systems with high-quality output verification can mutate graphs more aggressively
(add/remove nodes, change topology) because they can catch degradation early.

**SynthOrg validation**: Partially confirmed. The quality scoring system (L2+L3 in
`src/synthorg/engine/quality/`) provides per-step quality signals. The hybrid loop's replan
trigger uses these signals to decide whether to add a replan step (graph mutation). However,
the quality verifier's confidence is not used to modulate mutation aggressiveness; the
replan threshold is fixed, not adaptive to verifier confidence.

**Implication**: As the quality scoring system matures, consider making the replan threshold
adaptive to verifier confidence. High-confidence quality signal -> allow more replans.
Low-confidence signal -> conserve budget.

### Finding 3: Selection/Pruning from a Super-Graph Beats Unconstrained Generation

**Claim**: Starting from a well-designed set of node/edge templates and selecting subsets
outperforms generating arbitrary workflows from scratch.

**SynthOrg validation**: Strongly confirmed. The Company YAML and 30 built-in roles in
`src/synthorg/core/role_catalog.py` are a super-graph of organizational patterns. Template
packs in `api/controllers/template_packs.py` apply curated patterns. The meeting protocols
(3 variants) and loop types (3 variants) are a bounded selection space rather than
open-ended generation.

**Implication**: Adding more template packs and expanding the super-graph is a higher-value
investment than adding more free-form configuration options.

### Finding 4: Quality-Cost Tradeoffs Must Be Explicit with Hard Budget Caps

**Claim**: Agentic workflows need explicit Pareto frontier navigation between output quality
and token cost, with hard caps preventing runaway spending.

**SynthOrg validation**: Confirmed. The budget system has hard caps at multiple levels
(per-task, per-agent daily, monthly hard stop). Model auto-downgrade is an explicit
quality-cost tradeoff. The `DegradationConfig` and quota degradation strategies
(alert/fallback/queue) are explicit Pareto navigation mechanisms. The coordination metrics
(Amdahl ceiling, straggler gap) provide efficiency bounds.

**Implication**: The existing budget architecture is sound. The missing piece is exposing
the quality-cost tradeoffs via the REST API: specifically, `GET /tasks/{id}` response
and the `CoordinationResult` Python type should surface cost, quality, and efficiency
metadata (estimated cost, actual cost, quality score, Amdahl ceiling, straggler gap).
See #688 coordination metrics gap (Gap G4) for the full scoping.

---

## Structural Credit Assignment

### Problem Statement

In multi-agent task pipelines, when a downstream subtask fails, it is not always clear
whether the root cause is:
1. **Direct failure**: The assigned agent's execution failed
2. **Upstream contamination**: The agent received poor-quality input from a predecessor
3. **Coordination overhead**: The routing decision created an inefficient handoff
4. **Quality gate propagation**: The agent passed quality gates but the downstream consumer
   found a defect

SynthOrg currently attributes all failure information to the executing agent's
`TaskExecution`:

- `infer_failure_category()` in `src/synthorg/engine/recovery.py` is keyword-based
  heuristic classification applied per-execution, not per-agent in a coordination run
- `RecoveryResult` captures one `failure_category` per execution
- `CoordinationResult` has `CoordinationPhaseResult` per phase but no per-agent attribution
- The 5-pillar evaluation in `src/synthorg/hr/evaluation/evaluator.py` scores agents over
  time windows, not for specific pipeline failures

### Proposed Design

**AgentContribution model** integrates with `CoordinationResult`:

Note: `CoordinationResult` has `model_config = ConfigDict(frozen=True)`. Adding
`agent_contributions` directly is a breaking change. The recommended approach is a
separate wrapper: `CoordinationResultWithAttribution(result: CoordinationResult,
agent_contributions: tuple[AgentContribution, ...])`, stored and returned in place of
the bare result by `_post_execution_pipeline`. This preserves immutability and avoids
migrating existing persisted `CoordinationResult` records.

```python
class AgentContribution(BaseModel):
    """Per-agent attribution within a coordination run."""
    agent_id: str
    subtask_id: str
    contribution_score: float  # 0.0 = no contribution, 1.0 = fully responsible
    failure_attribution: Literal[
        "direct", "upstream_contamination", "coordination_overhead", "quality_gate"
    ] | None
    evidence: str | None  # pointer to error findings or quality signal
```

**Attribution algorithm**:

1. Topological sort of `DecompositionResult.dependency_edges`
2. For each failing subtask, walk backward through dependency edges
3. Classify: if predecessor's `StepQualitySignal` is low, attribute "upstream_contamination"
   to the predecessor; if the local execution raised directly, attribute "direct" to the
   executing agent
4. Coordination overhead: if `CoordinationMetrics.error_amplification > threshold`, attribute
   a fraction to topology mismatch
5. Normalize contribution scores so they sum to 1.0 across the pipeline

**Integration points**:
- Run as part of `_post_execution_pipeline` after coordination completes
- Feed `AgentContribution` into `PerformanceTracker.record_task_metric()` for trend detection
- Surface in `GET /tasks/{id}` response metadata for operator inspection

**Scope note**: This is a research recommendation, not an implementation spec. The
minimum viable version introduces a `CoordinationResultWithAttribution` wrapper containing
the original (immutable) `CoordinationResult` plus a list of `AgentContribution` objects
populated per-agent subtask result using the existing keyword-heuristic from
`infer_failure_category()`. This preserves `CoordinationResult` immutability with no changes
to the frozen model.

---

## Agent Pruning / Dropout Evaluation

### Current State

The infrastructure for agent removal exists and is production-grade:

- `src/synthorg/hr/offboarding_service.py`: `OffboardingService`, the full pipeline for agent
  removal (task reassignment, memory archival, team notification, status termination)
- `src/synthorg/core/enums.py`: `FiringReason.PERFORMANCE` exists as a reason code
- `src/synthorg/hr/performance/tracker.py`: `PerformanceTracker` providing rolling windows, trend
  detection (Theil-Sen), quality and collaboration scoring

What does not exist: any automated trigger for `OffboardingService.offboard()` based on
performance data. `FiringReason.PERFORMANCE` is defined but never programmatically invoked.

### Pruning Signal Sources

Four signal categories that should drive pruning recommendations:

1. **Performance trend**: Theil-Sen slope below `declining_threshold` for the 30d window.
   Available from `AgentPerformanceSnapshot.quality_trend` in `hr/performance/tracker.py`.

2. **Utilization**: Tasks assigned relative to team size. Low-utilization agents are
   redundant overhead. Currently tracked via task records: a task-per-agent-per-window
   count would be the metric.

3. **Skill redundancy**: High Jaccard similarity of required skills with another agent on
   the team, combined with high routing substitutability (how often could the other agent
   have handled this agent's tasks based on `RoutingCandidate.score`).

4. **Budget pressure**: Monthly utilization approaching threshold. When a team exceeds its
   budget allocation, pruning lowest-performing agents reduces future spend.

### Proposed Protocol

```python
PruningEvaluation (new model)
  agent_id: str
  pruning_score: float   # 0.0 = retain, 1.0 = prune
  signals: list[PruningSignal]  # which criteria triggered
  recommendation: Literal["PRUNE", "RETAIN", "MONITOR"]

PruningPolicy (new model)
  quality_decline_threshold: float   # Theil-Sen slope below which to flag
  utilization_minimum: float         # tasks-per-window below which to flag
  redundancy_threshold: float        # Jaccard similarity above which to flag
  cooldown_days: int                 # min time between pruning decisions
  min_team_size: int                 # never prune below this team size

PruningService (new service)
  evaluate(agent_id) -> PruningEvaluation
    # Reads from PerformanceTracker, RoutingHistory
  recommend_pruning(department_name) -> list[PruningEvaluation]
    # Scans all agents in department, returns sorted by pruning_score
```

**Human approval gate**: Any `PruningEvaluation` with `recommendation="PRUNE"` creates an
`ApprovalItem` following the same approval pattern used by the hiring and promotion
pipelines. Required fields:

- `id`: unique UUID per `PruningEvaluation`
- `title`: short summary, e.g. `"Prune agent {agent_id} ({reason})"`
- `description`: rationale from `PruningEvaluation.signals` (quality decline slope,
  utilization, Jaccard overlap), affected team, and safety constraint check results
- `requested_by`: the `PruningService` identifier or calling system
- `action_type`: `"org:prune"`
- `risk_level`: `ApprovalRiskLevel.MEDIUM`
- `created_at`: ISO 8601 timestamp

Pruning is never fully automated; it is recommendation plus human approval.

### Safety Constraints

1. **Minimum team size**: Never prune if the team would fall below `min_team_size`
2. **Unique skill protection**: Never prune the last agent with a required skill that no
   other agent possesses (validated against `RoutingHistory.required_skills`)
3. **Mid-task protection**: Flag if agent has active task assignments; recommendation becomes
   "MONITOR" instead of "PRUNE" until tasks complete
4. **Cooldown period**: `PruningPolicy.cooldown_days` prevents consecutive pruning decisions
5. **Seniority preference**: When multiple agents are candidates, prefer pruning
   lower-seniority agents first

### Relationship to HR Module

Pruning is the inverse of hiring. The same `OffboardingService` used for voluntary
departures handles performance-based pruning. The `FiringReason.PERFORMANCE` code exists
precisely for this. The only new infrastructure needed is:
- `PruningService` to evaluate signals and generate recommendations
- `PruningPolicy` config model
- API endpoint to surface recommendations (`GET /agents/pruning-recommendations` or similar)
- The approval flow reuses the existing `ApprovalItem` infrastructure

### Relationship to ACG AgentDropout and Adaptive Graph Pruning

The ACG survey discusses AgentDropout (removing underperforming agents mid-run) and
Adaptive Graph Pruning (removing redundant workflow nodes). SynthOrg's proposed pruning
is more conservative; it operates at the HR layer (between runs) rather than mid-execution.
Mid-execution dropout (removing an agent after it has started a subtask) is significantly
more complex due to task handoff and context transfer requirements. The inter-run HR pruning
is the correct first implementation target.

---

## Vocabulary Adoption Recommendation

### Options Considered

1. **Code rename**: Replace SynthOrg-specific terms with ACG terms in class/method names
2. **Docs-only**: Add an ACG glossary to `docs/architecture/` mapping terms
3. **Bidirectional glossary**: Document both terminologies, neither renamed

### Recommendation: Bidirectional Glossary (Option 3)

The ACG formalism is a useful external reference vocabulary but is incomplete for
SynthOrg's concepts (trust, personality, autonomy, memory, prompt profiles). Code-level
renaming would:
- Remove domain-specific precision (e.g., "HybridLoop" is more descriptive than
  "ConditionalACGNode")
- Break existing API contracts and test surface
- Gain little because the ACG vocabulary is not user-facing

The value of ACG is in research alignment (citing papers using shared vocabulary) and
gap identification (seeing what SynthOrg lacks in the ACG model). Both goals are served
by a bidirectional glossary without code changes.

**Action**: Add `docs/architecture/acg-glossary.md` mapping ACG concepts to SynthOrg
equivalents (using the mapping table from this document). Reference in the design spec
and in research communications.

**Formal node typing** is the one ACG concept that could benefit from a lightweight code
adoption. Introducing a `NodeType` enum with values `LLM_CALL`, `TOOL_INVOCATION`,
`QUALITY_CHECK`, `BUDGET_CHECK`, `STAGNATION_CHECK` and tagging `TurnRecord` with the
node types executed in that turn would improve execution trace analysis without
significant refactoring. This is optional but would directly enable structural credit
assignment (knowing which node type failed).

**Backward compatibility**: `TurnRecord` is part of execution traces and may be
persisted. The `node_types` field must be added as **optional with a default** (e.g.,
`node_types: tuple[NodeType, ...] = ()`) so existing records remain valid without
migration. Serialization/deserialization must tolerate the absent field. Consumers
(trace analyzers, evaluation pipelines) should treat an empty tuple as "unknown
composition" rather than erroring.

---

## Summary of Recommendations

1. **Bidirectional ACG glossary** in `docs/architecture/acg-glossary.md`; no code changes
2. **Structural credit assignment**: Add `CoordinationResultWithAttribution` wrapper
   (frozen `CoordinationResult` + `AgentContribution` list); run attribution in
   `_post_execution_pipeline`; feed into `PerformanceTracker`
3. **Agent pruning**: Implement `PruningService` + `PruningPolicy`; wire to existing
   `OffboardingService`; human approval gate required
4. **Optional node typing**: Add `NodeType` enum to `TurnRecord` for richer trace analysis
5. **Adaptive quality-cost tradeoff**: Make hybrid loop replan threshold adaptive to
   quality verifier confidence (longer-term)
