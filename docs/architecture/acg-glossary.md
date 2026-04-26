---
description: Bidirectional mapping between ACG formalism concepts and SynthOrg equivalents.
---

# ACG Glossary

Bidirectional mapping between the Agentic Computation Graph (ACG) formalism
([arXiv:2603.22386](https://arxiv.org/abs/2603.22386)) and SynthOrg's architecture.

For the full evaluation including survey findings validation, structural credit assignment
design, and agent pruning recommendations, see
[`docs/research/acg-formalism-evaluation.md`](../research/acg-formalism-evaluation.md).

---

## ACG to SynthOrg

### Core Graph Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| ACG Template | `CompanyConfig` + Company YAML / `WorkflowDefinition` | `core/company.py`, `config/schema.py` | Partial | ACG templates are graph-level (workflow topology). SynthOrg's YAML is org-level (agent roster, tool permissions, budget). `WorkflowDefinition` is the closer analogue for workflow templates. |
| Realized Graph | `AgentContext` + `TaskExecution` + `CoordinationResult` | `engine/context.py`, `engine/coordination/models.py` | Strong | The realized graph IS the running state: context, history, accumulated cost, current position. Multi-agent coordination adds `CoordinationPhaseResult` per phase. |
| Execution Trace | `tuple[TurnRecord, ...]` in `ExecutionResult` + observability events | `engine/loop_protocol.py`, `observability/events/` | Strong | SynthOrg's trace is richer than ACG baseline: per-turn cost, token usage, tool fingerprints, stagnation signals, quality scores. numerous event constant domains (see `observability/events/`). |
| Nodes (atomic actions) | LLM calls (`call_provider`), tool invocations (`execute_tool_calls`), validation gates (`check_budget`, `check_stagnation`) | `engine/loop_helpers.py` | Partial | Node typing is implicit in loop control flow, not a first-class abstraction. There is no `Node` type; actions are identified by function names and turn records. |
| Edges (control/data flow) | `SubtaskDefinition.dependencies` DAG, `DecompositionPlan.dependency_edges` | `engine/decomposition/models.py` | Strong (multi-agent) | Edges are explicit in multi-agent decomposition (dependency DAG). Implicit in single-agent loops (sequential execution order, no formal edge representation). |
| Scheduling Policies | `AutoLoopConfig` + `select_loop_type()` + `CoordinationConfig` + `AutoTopologyConfig` | `engine/loop_selector.py`, `engine/routing/models.py` | Strong | Three-way loop selection (react/plan-execute/hybrid) and topology selection (SAS/centralized/decentralized/context-dependent) are scheduling policies. Budget-aware downgrade is a resource-constrained policy. |

### Dynamic Behavior Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| Conditional branching | HybridLoop replan decisions, PlanExecuteLoop step completion | `engine/hybrid_loop.py`, `engine/hybrid_helpers.py` | Partial | Branching is embedded in loop logic (replan if step fails), not graph-level conditional edges. No formal "if node X succeeds, take edge Y" representation. |
| Parallel composition | `ParallelExecutor`, `CoordinationWave`, `asyncio.TaskGroup` | `engine/parallel.py`, `engine/coordination/models.py` | Strong | Parallel waves in coordination are first-class. `ParallelExecutor` handles concurrent subtask dispatch with `fail_fast` semantics. |
| Graph mutation | Hybrid replanning (`attempt_replan`), stagnation correction injection | `engine/hybrid_helpers.py`, `engine/stagnation/` | Partial | Replanning mutates the execution plan (new subtask list). Stagnation correction injects a new message. These are graph mutations but are not described in those terms. |
| Termination conditions | `TerminationReason` enum (7 values: COMPLETED, MAX_TURNS, BUDGET_EXHAUSTED, SHUTDOWN, PARKED, STAGNATION, ERROR) | `engine/loop_protocol.py` | Strong | Richer than typical ACG termination models. 7 named reasons provide precise signal for recovery and routing decisions. |

### Resource and Cost Concepts

| ACG Concept | SynthOrg Equivalent | Source | Fidelity | Notes |
|---|---|---|---|---|
| Node cost | `TurnRecord.cost` per turn, `TokenUsage` per completion | `engine/loop_protocol.py`, `providers/models.py` | Strong | Per-turn cost tracking with provider breakdown. Accumulated over execution via `ctx.accumulated_cost`. |
| Resource constraints | `BudgetEnforcer` (3-layer), quota degradation, context budget | `budget/enforcer.py`, `engine/context_budget.py` | Strong | SynthOrg's resource model is more sophisticated than ACG: multi-layer enforcement, per-agent daily limits, context fill tracking, risk budget. |
| Quality-cost tradeoffs | Budget-aware loop downgrade (hybrid->plan_execute at 80%), model auto-downgrade, quota degradation strategies | `engine/loop_selector.py`, `budget/enforcer.py` | Strong | Explicit tradeoff mechanisms with hard budget caps. Downgrade only at task boundaries (consistency guarantee). |

---

## SynthOrg to ACG

Reverse lookup for readers starting from SynthOrg terminology.

| SynthOrg Concept | ACG Equivalent | Notes |
|---|---|---|
| `CompanyConfig` / Company YAML | ACG Template | Org-level; `WorkflowDefinition` maps more precisely to graph-level templates |
| `AgentContext` + `TaskExecution` | Realized Graph | Running state with full context |
| `TurnRecord` tuple | Execution Trace | Per-turn cost/token data exceeds ACG baseline |
| LLM calls, tool invocations, validation gates | Nodes | Implicit typing via function names, not a `Node` type |
| `SubtaskDefinition.dependencies` | Edges | Explicit in multi-agent DAG, implicit in single-agent |
| `AutoLoopConfig` + `select_loop_type()` | Scheduling Policies | 3-way loop + topology selection |
| `HybridLoop` replan | Conditional branching + Graph mutation | Embedded in loop logic |
| `ParallelExecutor`, `CoordinationWave` | Parallel composition | First-class with `fail_fast` |
| `TerminationReason` (7 values) | Termination conditions | Richer taxonomy |
| `BudgetEnforcer` (3-layer) | Resource constraints | Multi-layer enforcement exceeds ACG |
| `TurnRecord.cost`, `TokenUsage` | Node cost | Per-turn + per-completion |
| Budget-aware downgrade | Quality-cost tradeoffs | Task-boundary-only downgrade |

---

## SynthOrg Extensions Beyond ACG

The following SynthOrg concepts have no equivalent in the ACG formalism:

| Concept | Module | Description |
|---|---|---|
| **Progressive trust** | `security/trust/service.py` | Agent trust levels (RESTRICTED/STANDARD/ELEVATED) with mandatory human approval for promotion. |
| **Personality and behavioral config** | `core/personality.py` | Big Five traits + behavioral enums affecting decision style. |
| **Memory injection** | `memory/retrieval/` | Episodic and procedural memory retrieval shaping context before execution. |
| **Prompt profiles** | `engine/prompt/profiles.py` | Verbosity adaptation by model tier. |
| **Autonomy levels** | `security/autonomy/resolver.py` | 4 presets (full/semi/supervised/locked) with tool permission gating. |

---

## ACG Concepts SynthOrg Handles Differently

Where fidelity is "Partial," SynthOrg implements the concept but through different
abstractions than ACG prescribes:

- **Node typing**: ACG defines explicit node types. SynthOrg's nodes are implicit in loop
  control flow; actions are identified by function names and turn records, not a `Node`
  type. A lightweight `NodeType` enum (LLM_CALL, TOOL_INVOCATION, QUALITY_CHECK, etc.)
  on `TurnRecord` is a recommended future addition.

- **Conditional branching**: ACG uses graph-level conditional edges. SynthOrg embeds
  branching in loop logic (replan if step fails), without formal "if node X succeeds,
  take edge Y" representation.

- **Graph mutation**: ACG describes runtime graph topology changes. SynthOrg's replanning
  and stagnation correction are functionally equivalent but are not described in graph
  mutation terms internally.
