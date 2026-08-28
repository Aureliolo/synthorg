---
title: "Evaluating the Agentic Computation Graph (ACG) Formalism for SynthOrg Engine Vocabulary, Structural Credit Assignment, and Agent Pruning"
issue: 848
source: "https://huggingface.co/papers/2603.22386"
date: 2026-04-07
related: [690]
---

# ACG Formalism Evaluation for SynthOrg Engine Architecture

!!! note "Dated snapshot"

    This evaluation is a point-in-time record. References to progressive trust and seniority-based agent selection describe designs that have since been removed; authority now derives from the role reporting graph (see [HR & Agent Lifecycle](../design/hr-lifecycle.md)).

## Context

The IBM/RPI survey "From Static Templates to Dynamic Runtime Graphs" (arXiv:2603.22386,
companion repository: [IBM awesome list](https://github.com/IBM/awesome-agentic-workflow-optimization))
introduces the Agentic Computation Graph (ACG) formalism to unify the vocabulary for
describing agent workflows: from static templates to dynamic runtime graphs, scheduling
policies, execution traces, and mutation strategies. SynthOrg has all these concepts but
expresses them through domain-specific names (execution loops, decomposition plans, turn
records, coordination topology). This evaluation assesses whether adopting ACG vocabulary would
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
| **Realised Graph** | `AgentContext` + `TaskExecution` + `CoordinationResult` | `src/synthorg/engine/context.py`, `src/synthorg/engine/coordination/models.py` | Strong | The realised graph IS the running state: context, history, accumulated cost, current position. Multi-agent coordination adds `CoordinationPhaseResult` per phase. |
| **Execution Trace** | `tuple[TurnRecord, ...]` in `ExecutionResult` + observability events | `src/synthorg/engine/loop_protocol.py`, `src/synthorg/observability/events/` | Strong | SynthOrg's trace is richer than ACG baseline: per-turn cost, token usage, tool fingerprints, stagnation signals, quality scores. 100+ event constant domains. |
| **Nodes (atomic actions)** | LLM calls (`call_provider`), tool invocations (`execute_tool_calls`), validation gates (`check_budget`, `check_stagnation`) | `src/synthorg/engine/loop_helpers.py` | Partial | Node typing is implicit in loop control flow, not a first-class abstraction. There is no `Node` type; actions are identified by function names and turn records. |
| **Edges (control/data flow)** | `SubtaskDefinition.dependencies` DAG, `DecompositionPlan.dependency_edges` | `src/synthorg/engine/decomposition/models.py` | Strong for multi-agent | Edges are explicit in multi-agent decomposition (dependency DAG). Implicit in single-agent loops (sequential execution order, no formal edge representation). |
| **Scheduling Policies** | `AutoLoopConfig` + `select_loop_type()` + `CoordinationConfig` + `AutoTopologyConfig` | `src/synthorg/engine/loop_selector.py`, `src/synthorg/engine/routing/models.py` | Strong | Per-complexity loop selection (react/openhands) and topology selection (SAS/centralised/decentralised/context-dependent) are scheduling policies. *(Loop selection has since been removed: one loop ships, so there is nothing to schedule between.)* |

### Dynamic Behaviour Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| **Conditional branching** | Loop termination checks, stagnation intervention verdicts | `src/synthorg/engine/react_loop.py`, `src/synthorg/engine/loop_control_helpers.py` | Partial | Branching is embedded in loop logic, not graph-level conditional edges. No formal "if node X succeeds, take edge Y" representation. |
| **Parallel composition** | `ParallelExecutor`, `CoordinationWave`, `asyncio.TaskGroup` | `src/synthorg/engine/parallel.py`, `src/synthorg/engine/coordination/models.py` | Strong | Parallel waves in coordination are first-class. `ParallelExecutor` handles concurrent subtask dispatch with `fail_fast` semantics. |
| **Graph mutation** | Stagnation correction injection, mid-flight steering adoption | `src/synthorg/engine/stagnation/`, `src/synthorg/engine/intervention/loop_hook.py` | Partial | Both inject a new message into a running execution. These are graph mutations but are not described in those terms. |
| **Termination conditions** | `TerminationReason` enum (9 values: COMPLETED, MAX_TURNS, BUDGET_EXHAUSTED, SHUTDOWN, PARKED, STAGNATION, CANCELLED, ERROR, NO_OP) | `src/synthorg/engine/loop_protocol.py` | Strong | Richer than typical ACG termination models. 9 named reasons provide precise signal for recovery and routing decisions. |

### Resource and Cost Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| **Node cost** | `TurnRecord.cost` per turn, `TokenUsage` per completion | `src/synthorg/engine/loop_protocol.py`, `src/synthorg/providers/models.py` | Strong | Per-turn cost tracking with provider breakdown. Accumulated over execution via `ctx.accumulated_cost`. |
| **Resource constraints** | `BudgetEnforcer` (pre-flight + in-flight), quota degradation, context budget | `src/synthorg/budget/enforcer.py`, `src/synthorg/engine/context_budget.py` | Strong (exceeds ACG) | SynthOrg's resource model is more sophisticated than ACG: multi-layer enforcement, per-agent daily limits, context fill tracking, risk budget. |
| **Quality-cost tradeoffs** | Capability-matched agent selection, quota degradation strategies | `src/synthorg/engine/routing_policy/capability_policy.py`, `src/synthorg/budget/enforcer.py` | Strong | Explicit tradeoff mechanisms with hard budget caps. The tradeoff is made when work is assigned; a running agent's binding is never rewritten. |

### Concepts SynthOrg Has That ACG Does Not Capture

- **Progressive trust**: agent trust levels (RESTRICTED/STANDARD/ELEVATED) with human
  approval for promotion. No ACG equivalent.
- **Personality and behavioural configuration**: `PersonalityConfig` with Big Five + behavioural
  enums affecting decision style. No ACG equivalent. *(Since removed: the personality
  surface was deleted whole.)*
- **Memory injection**: episodic and procedural memory retrieval shaping context before
  execution. No ACG equivalent.
- **Prompt profiles**: verbosity adaptation by capability rung. No ACG equivalent.
- **Autonomy levels**: 4 presets (full/semi/supervised/locked) with tool permission gating.
  No ACG equivalent.

---

## ACG Survey Findings Validation

The survey identifies four structural findings that apply to SynthOrg's architecture:

### Finding 1: Structural Improvements > Prompt Refinement When Scaffold Is Poorly Matched

**Claim**: When an agent workflow uses the wrong graph structure (e.g., sequential when
parallel would be correct), prompt engineering cannot compensate. Structural changes yield
greater gains.

**SynthOrg validation**: Confirmed by the loop selector, which maps task complexity to
loop type. Which loop suits which complexity is deliberately left to measurement: the
inner-loop A/B harness ranks the shipped loops on the same coding work and its
scoreboard is applied as `engine.loop_complexity_overrides`, so the routing is set from
evidence rather than judgement. *(Since removed: the A/B ran, one loop won, and both the
selector and the harness went with it. The measurement is preserved at
[`inner-loop-ab-recording.md`](inner-loop-ab-recording.md).)*

**Implication**: While two loops shipped, the selector was doing real structural work,
and adding complexity to system prompts for tasks that suited the other loop was not a
substitute. The measurement settled which loop that was, so the structural lever is now
the topology selector rather than the loop selector; the finding stands, its subject
moved.

### Finding 2: Strong Verifiers Enable More Aggressive Graph Mutation

**Claim**: Systems with high-quality output verification can mutate graphs more aggressively
(add/remove nodes, change topology) because they can catch degradation early.

**SynthOrg validation**: Partially confirmed at a coarser grain than the claim assumes.
The quality scoring system (L2+L3 in `src/synthorg/engine/quality/`) provides per-step
quality signals, and the initiative tail mutates the graph when an item stalls: the
replan trigger opens a fresh plan rather than adjusting a running one. Verifier
confidence does not modulate how aggressively that fires.

**Implication**: As the quality scoring system matures, consider making the replan
trigger's threshold adaptive to verifier confidence. High-confidence quality signal ->
replan sooner. Low-confidence signal -> conserve budget.

### Finding 3: Selection/Pruning from a Super-Graph Beats Unconstrained Generation

**Claim**: Starting from a well-designed set of node/edge templates and selecting subsets
outperforms generating arbitrary workflows from scratch.

**SynthOrg validation**: Strongly confirmed. The Company YAML and 33 built-in roles in
`src/synthorg/core/role_catalog.py` are a super-graph of organisational patterns. Template
packs in `api/controllers/template_packs.py` apply curated patterns. The meeting protocols
(3 variants) and loop types (React and OpenHands) are a bounded selection space rather
than open-ended generation. *(Since removed: the meeting stack was deleted whole, and one
loop ships, so neither is a selection space any more. The super-graph argument stands on
the roles and template packs alone.)*

**Implication**: Adding more template packs and expanding the super-graph is a higher-value
investment than adding more free-form configuration options.

### Finding 4: Quality-Cost Tradeoffs Must Be Explicit with Hard Budget Caps

**Claim**: Agentic workflows need explicit Pareto frontier navigation between output quality
and token cost, with hard caps preventing runaway spending.

**SynthOrg validation**: Confirmed. The budget system has hard caps at multiple levels
(per-task, per-agent daily, monthly hard stop). The capability ladder is the explicit
quality-cost tradeoff: it prefers the cheapest agent that clears the rung the work
demands, and refuses rather than conceding above the park floor. The `DegradationConfig`
strategies (alert/queue) and the Pareto frontier's advisory rebinding callouts are the
other navigation mechanisms. The coordination metrics (Amdahl ceiling, straggler gap)
provide efficiency bounds.

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

SynthOrg attributes all failure information to the executing agent's
`TaskExecution`:

- `infer_failure_category()` in `src/synthorg/engine/failure_classification.py` is keyword-based
  heuristic classification applied per-execution, not per-agent in a coordination run
- `RecoveryResult` captures one `failure_category` per execution
- `CoordinationResult` has `CoordinationPhaseResult` per phase but no per-agent attribution
- The performance tracker in `src/synthorg/hr/performance/tracker.py` scores agents over
  time windows, not for specific pipeline failures

### Proposed Design

**AgentContribution model** integrates with `CoordinationResult`:

Note: `CoordinationResult` has `model_config = ConfigDict(frozen=True)`. Adding
`agent_contributions` directly is a breaking change. The recommended approach is a
separate wrapper: `CoordinationResultWithAttribution(result: CoordinationResult,
agent_contributions: tuple[AgentContribution, ...])`. It is stored and returned in
place of the bare result by `_post_execution_pipeline`. This preserves immutability and avoids
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
5. Normalise contribution scores so they sum to 1.0 across the pipeline

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
- `src/synthorg/hr/enums.py`: `FiringReason.PERFORMANCE` exists as a reason code
- `src/synthorg/hr/performance/tracker.py`: `PerformanceTracker` providing rolling windows, trend
  detection (Theil-Sen), and quality/collaboration scoring

What does not exist: any automated trigger for `OffboardingService.offboard()` based on
performance data. `FiringReason.PERFORMANCE` is defined but never programmatically invoked.

### Pruning Signal Sources

Four signal categories that should drive pruning recommendations:

1. **Performance trend**: Theil-Sen slope below `declining_threshold` for the 30d window.
   Available from `AgentPerformanceSnapshot.quality_trend` in `hr/performance/tracker.py`.

2. **Utilisation**: Tasks assigned relative to team size. Low-utilisation agents are
   redundant overhead. Tracked via task records: a task-per-agent-per-window
   count would be the metric.

3. **Skill redundancy**: High Jaccard similarity of required skills with another agent on
   the team, combined with high routing substitutability (how often could the other agent
   have handled this agent's tasks based on `RoutingCandidate.score`).

4. **Budget pressure**: Monthly utilisation approaching threshold. When a team exceeds its
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
  utilisation, Jaccard overlap), affected team, and safety constraint check results
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

- Remove domain-specific precision (e.g., "ReactLoop" is more descriptive than
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
migration. Serialisation/deserialisation must tolerate the absent field. Consumers
(trace analysers, evaluation pipelines) should treat an empty tuple as "unknown
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
5. **Adaptive quality-cost tradeoff**: Make the initiative-tail replan threshold adaptive
   to quality verifier confidence (longer-term)
