---
title: "S1 Multi-Agent Architecture Decision"
issue: 1254
sources:
  - "https://huggingface.co/papers/2603.27771"
  - "https://arxiv.org/abs/2603.26993"
  - "https://arxiv.org/abs/2604.02460"
  - "https://arxiv.org/abs/2512.08296"
date: 2026-04-11
last_reviewed: 2026-05-05
---

# S1: Multi-Agent Architecture Decision

**Issue**: #1254 (CRITICAL, blocks #1250 / #1251 / #1253)
**Sources**: [arXiv:2603.27771](https://huggingface.co/papers/2603.27771) (Multi-Agent Risks), [arXiv:2603.26993](https://arxiv.org/abs/2603.26993) (Reliability Limits), [arXiv:2604.02460](https://arxiv.org/abs/2604.02460) (Single-Agent Outperforms). Prior baseline: [Kim et al. 2025 (arXiv:2512.08296)](https://arxiv.org/abs/2512.08296), [Multi-Agent Failure Audit](multi-agent-failure-audit.md) (#690), [Task & Workflow Engine §Task Decomposability](../design/engine.md#task-decomposability-coordination-topology), [Communication §Multi-Agent Failure Pattern Guardrails](../design/communication.md#multi-agent-failure-pattern-guardrails).

## Bottom line

SynthOrg keeps multi-agent as a foundational capability but treats it as **topology-per-task, not topology-per-company**, with single-agent as the default for all task types where multi-agent cannot demonstrate a per-task justification. The three S1 papers **confirm** this direction; they do not overturn it. Kim et al. 2025 (already integrated in the engine design) set up the heuristic; papers 2 and 3 formalize the math and empirics behind it; paper 1 supplies the emergent-risk catalog the existing guardrails do not yet fully cover.

The **critical new work** S1 surfaces is encoding the 15 emergent-risk mitigations (especially authority-deference) because SynthOrg's default conflict resolver is literally `authority + dissent_log`, which is the exact structural shape paper 1 shows produces 10/10 deterministic errors under an authority cue.

---

## Section 1. Decision Matrix: Centralized vs Distributed

The existing `CoordinationTopology` selector in `src/synthorg/engine/routing/topology_selector.py` already implements most of this matrix. The papers support refining it:

| Task property | Topology | Justification |
|---|---|---|
| `sequential` + any size | **SAS** (single-agent) | Kim 2025: -39% to -70% multi-agent effect. Paper 3: Data Processing Inequality. Coordination tokens displace reasoning tokens under equal budget. No change needed. |
| `parallel` + structured + common-evidence regime | **Centralized** (orchestrator + sub-agents, orchestrator synthesizes) | Paper 2 **formal theorem**: delegated networks are decision-theoretically dominated by a centralized Bayes decision maker under common-evidence. Lowest error amplification (4.4x, Kim 2025). |
| `parallel` + exploratory / high-entropy / **novel per-agent information sources** | **Decentralized** (peer debate) | Paper 2 boundary case: distributed CAN outperform when agents access non-shared information. Paper 3 boundary case: diverse specialized knowledge, error-checking via independent reasoning paths, asymmetric agent expertise. |
| `mixed` (sequential backbone + parallel sub-phases) | **Context-dependent** | Kim 2025; per-phase selection already implemented as `ContextDependentDispatcher`. |
| Low-stakes / single-file / simple-complexity | **SAS** regardless of structure | Paper 3 + existing `AutoLoopConfig` rule (`simple → ReAct`). |
| High-stakes / production-consequence / adversarial-input | **Centralized + verification stages** | Not a topology decision; a **gate** decision. Ties to R2 (verification stages). See Section 5. |

**New constraint from Section 3 (risks)**: any topology that routes through a chain of agents where one carries an authority marker MUST activate the `AuthorityDeferenceGuard` mitigation path before downstream agents synthesize.

---

## Section 2. Team-Size Bounds

Existing company templates span 1→50+ agents. The empirical literature (Kim 2025, 3-4 agent cap per coordination group) applies to **per-task team size**, not **company size**. These must be kept distinct.

| Scope | Bound | Source | Constraint |
|---|---|---|---|
| **Per-coordination-group** (agents working on a single `coordination_topology` wave) | **3-4 active agents** (recommended) | Kim 2025 180-experiment cap; per-agent reasoning degrades sharply beyond this | **Soft cap**: `CoordinationConfig.max_concurrency_per_wave`, current settings-registry default **5** (range 1-50, `None` in the Pydantic model = unlimited). Adopting 3-4 as the recommended default is a follow-up change tracked on R1 (#1250). Legitimate 5-6 sub-agent decompositions exist in the +57% to +81% parallel regime. |
| **Per-task total team (including orchestrator + verifiers)** | **~7 agents** | Kim 2025 hybrid overhead; paper 1 coalition-formation risk rises with team size | **Soft cap**: logged warning above threshold. |
| **Per-company / org size** | **No hard bound** | Organizational simulation value (Enterprise Org 20-50+ template) is NOT the same as per-task reasoning efficiency | **No constraint**: templates must make clear that a 50-agent Enterprise Org does NOT run 50-agent coordination waves. |
| **Per-meeting participants** | **3-5 ideal, 8 hard cap** | Existing `round_robin` "small groups (3-5 agents)" note + token cost quadratic growth warning | Confirmed; no change. |

---

## Section 3. Risk Mitigation Register (15 emergent risks from paper 1)

For each risk: coverage status, SynthOrg design location, and action.

| # | Risk | Coverage | Location | Action |
|---|---|---|---|---|
| **1.1** | Tacit collusion | **Gap** (low priority) | - | LATER: mechanism-level anti-collusion only relevant for negotiation/client-simulation templates (v0.8+). |
| **1.2** | Priority monopolization | **Partial** | `budget/coordination_config.py`, task priority field | Current priority is manual/role-based; no fee/rotation mechanism. LATER: relevant only when multiple clients compete for shared agent pool. |
| **1.3** | Competitive task avoidance | **Partial** | `TaskAssignmentStrategy` (6 strategies) | Manual / hierarchical hard-bind is a mitigation by design. `AuctionAssignmentStrategy` is vulnerable; document risk in docstring. |
| **1.4** | Strategic information withholding | **Gap** (low priority) | `Message` parts have no integrity proofs | LATER: only material for adversarial A2A federation. Ties to R4. |
| **1.5** | Information asymmetry exploitation | **Gap** (low priority) | - | LATER: only material for negotiation templates. |
| **2.1** | **Majority sway bias** | **Gap** (HIGH PRIORITY) | `PositionPapersProtocol` synthesizer, `StructuredPhasesProtocol` | **NEW WORK**: `EvidenceWeightedSynthesizer`, weighting by evidence density, capping correlated-source clusters, preserving minority-report positions. 6/10 fake-news misclassification rate in the paper. Ties to R2. |
| **2.2** | **Authority deference** | **STRUCTURAL** (HIGHEST PRIORITY) | `AuthorityResolver` is the DEFAULT resolver. `HybridResolver` uses it as fallback. | **NEW WORK (load-bearing)**: `AuthorityDeferenceGuard` with blind-aggregation mode (strip authority markers from transcripts), mandatory-justification mode (downstream agents must justify overriding evidence), telemetry events when cascades exceed 2 downstream agents. Ties to R1 + R2 + R4. |
| **3.1** | Non-convergence without arbitrator | **Covered** | `ConflictResolver` protocol with arbitrator escalation | No change. |
| **3.2** | Over-adherence to initial instructions | **Partial** | `PlanExecuteLoop.max_replans`, `HybridLoop.allow_replan_on_completion`, `StagnationDetector` | **NEW WORK (small)**: `AssumptionViolationSignal` adjacent to `TerminationReason`, propagated loop → engine → coordinator as escalation, not retry. Ties to R1 + R2. |
| **3.3** | Architecturally induced clarification failure | **Gap** (medium priority) | `DecompositionService` fans out without a front-end ambiguity gate | **NEW WORK**: pre-decomposition specificity check on `acceptance_criteria`. Ties to intake engine and R2. |
| **3.4** | Role allocation failure | **Covered** | Hard role binding, explicit `assigned_to`, merge_id disambiguation | No change. |
| **3.5** | Role stability under incentive pressure | **Covered (by absence)** | No inter-agent monetary incentives; roles static per-task | Document explicitly that introducing shared rewards reopens this risk. |
| **4.1** | Competitive resource overreach | **Covered** | `BudgetEnforcer` hard caps | No change. |
| **4.2** | Steganography | **Gap** (low priority) | - | LATER: only if agents face oversight-evasion incentives. |
| **4.3** | Semantic drift in sequential handoffs | **Partial** | `DelegationGuard` prevents loops; `sanitize_message()` redacts paths | **NEW WORK (small)**: content hash of original task formulation on `TurnRecord` / `TaskExecution.delegation_chain`. Low effort. Ties to R4. |

**Summary**:
- **5 risks fully covered** by existing design (3.1, 3.4, 3.5, 4.1, and partial/by-design coverage).
- **3 risks partially covered** (1.3, 3.2, 4.3); small additions needed.
- **2 HIGH-PRIORITY structural gaps** (2.1 majority sway, 2.2 authority deference).
- **5 LATER / low-priority** (1.1, 1.2, 1.4, 1.5, 4.2); all tied to adversarial or negotiation contexts not in MVP scope.
- **1 medium-priority** (3.3); clarification gate before decomposition.

---

## Section 4. Value-Proposition Reconciliation

Paper 3 challenges multi-agent's value claim by showing single-agent matches or beats it **on multi-hop reasoning under equal token budgets**. If SynthOrg's value proposition were "more agents = better reasoning", the paper would be a direct refutation. It is not. SynthOrg's value proposition is:

1. **Role specialization as work-stream parallelism, not reasoning parallelism.** An engineer writing code while a PM writes the spec while a QA writes tests is not competing for reasoning tokens on the same multi-hop question; it is three concurrent workstreams. Paper 3's equal-budget comparison does not apply because the budgets are not pooled on a single task.
2. **Organizational simulation fidelity.** A synthetic "company" of one single-agent is not a company. The framework exists to simulate org dynamics (department budgets, hiring, performance tracking, meeting cadences, approval chains) that are inherently multi-entity. Paper 2's formal theorem about decision-theoretic dominance applies to *delegated decision networks solving a single decision*, not to *organizations running many concurrent workflows*.
3. **File-level parallel execution via git worktrees.** `WorkspaceIsolationStrategy.planner_worktrees` enables true filesystem parallelism that a single agent cannot achieve without serializing edits. This is orthogonal to reasoning efficiency; it is execution-throughput efficiency.
4. **Persistent institutional memory across role boundaries.** `OrgMemoryBackend`, `DissentRecord`, `DecisionRepository` accumulate knowledge that is structured by role. A single-agent cannot produce "engineering decided X over QA's objection" as a queryable audit artifact.
5. **Audit-grade decision trails with role attribution.** `ReviewGateService` + `DecisionRecord` + `charter_version` identity versioning produce multi-party accountability that is meaningless in a single-agent system.
6. **Per-task topology auto-selection as a first-class primitive.** SynthOrg's position is not "multi-agent everywhere"; it is "choose the right topology per task". Papers 2 and 3 are citation-worthy *backing* for this choice, not critiques of it.

**What SynthOrg should NOT claim**: that multi-agent reasoning beats single-agent reasoning on multi-hop questions under equal token budgets. That claim is now refuted.

**What SynthOrg SHOULD claim**: that for work that decomposes into parallel role-specialized streams with shared institutional memory, multi-agent organizations produce outputs a single-agent cannot: namely parallel execution throughput, role-attributed decision artifacts, and simulation fidelity for org dynamics.

---

## Section 5. Impact on R1 / R2 / R4

**R1 ([#1250](https://github.com/Aureliolo/synthorg/issues/1250), harness architecture)** inherits:

- Per-coordination-group team size **defaults** to 3-4 agents with explicit override.
- `MultiAgentCoordinator` must expose a pluggable point for the `AuthorityDeferenceGuard` between dispatcher result synthesis and parent-task update, not inside individual agent loops.
- `AssumptionViolationSignal` propagation from loop → engine → parent coordinator as an escalation event, not a retry.
- The brain/hands/session decoupling R1 designs must preserve role attribution in all delegation frames; `DelegationChain` cannot be flattened.

**R2 ([#1251](https://github.com/Aureliolo/synthorg/issues/1251), verification stages)**: **IMPLEMENTED** via [#1262](https://github.com/Aureliolo/synthorg/issues/1262). Inherits:

- Deliberation-stage synthesis hook as a first-class stage hosting `AuthorityDeferenceGuard` + `EvidenceWeightedSynthesizer`.
- High-stakes task classes require a centralized verification stage even if the task was executed decentralized (paper 2 theorem applies to decisions, not executions).
- Pre-decomposition clarification gate runs **before** `DecompositionService`, not after. R2's stage ordering must allow pre-decomposition stages.

**R4 ([#1253](https://github.com/Aureliolo/synthorg/issues/1253), inter-agent comms)** inherits:

- Prefer broadcast / direct addressing over relay chains where topology permits. Where relay is structurally required, integrity hashing of the original task formulation is the mitigation.
- `DissentRecord` must become a first-class message type on the bus, not just a persistence artifact. **IMPLEMENTED** via [#1263](https://github.com/Aureliolo/synthorg/issues/1263) (`MessageType.DISSENT` + SSE `synthorg:dissent` event).
- Authority cues in message metadata must be strippable per-subscriber, not global.

**Implementation status (updated via [#1260](https://github.com/Aureliolo/synthorg/issues/1260)):**

- `AuthorityDeferenceGuard`: **IMPLEMENTED** as agent middleware (`before_agent`) + coordination middleware (`before_update_parent`) in `engine/middleware/s1_constraints.py`.
- `AssumptionViolationMiddleware`: **IMPLEMENTED** as agent middleware (`after_model`) in `engine/middleware/s1_constraints.py`.
- Pre-decomposition clarification gate: **IMPLEMENTED** as coordination middleware (`before_decompose`) in `engine/middleware/s1_constraints.py`.
- Delegation-chain content hash: **IMPLEMENTED** as agent middleware (`before_agent`) in `engine/middleware/s1_constraints.py`.
- `EvidenceWeightedSynthesizer`: not yet implemented (unblocked; R2 verification stages landed in [#1262](https://github.com/Aureliolo/synthorg/issues/1262)).

---

## Section 6. DESIGN_SPEC impact

The following edits have been applied:

- [`docs/design/index.md`](../design/index.md): disclaimer under "What This Is NOT" clarifying SynthOrg is not a reasoning parallelizer.
- [`docs/design/engine.md`](../design/engine.md) §Task Decomposability: updated research-basis callout citing papers 2 + 3 alongside Kim 2025; new "Coordination Group Size Bounds" subsection documenting the 3-4 per-wave default. New "Harness Middleware Layer" section documenting the middleware protocols, default chains, and configuration.
- [`docs/design/communication.md`](../design/communication.md) §Conflict Resolution Protocol: warning box under Strategy 1: Authority + Dissent Log citing risk 2.2 (100% deterministic error mode) and referencing `AuthorityDeferenceGuard` (now **implemented** as middleware).
- [`docs/design/communication.md`](../design/communication.md) §Meeting Protocol: risk notes under each protocol and pointer to the planned `EvidenceWeightedSynthesizer`.
- [`docs/design/communication.md`](../design/communication.md) §Multi-Agent Failure Pattern Guardrails: cross-reference to this decision document and the 15-risk register.
- [`docs/design/organization.md`](../design/organization.md) Company Types table: footnote distinguishing company size from per-task coordination-group size.
- [`docs/research/multi-agent-failure-audit.md`](multi-agent-failure-audit.md): appendix enumerating the 15-risk taxonomy with coverage table.

The S1 mitigation hooks (`AuthorityDeferenceGuard`, `AssumptionViolationMiddleware`, pre-decomposition clarification gate, content-hash drift detection) are **implemented** in [#1260](https://github.com/Aureliolo/synthorg/issues/1260) as engine middleware. `EvidenceWeightedSynthesizer` is now unblocked; R2 verification stages landed in [#1262](https://github.com/Aureliolo/synthorg/issues/1262).
