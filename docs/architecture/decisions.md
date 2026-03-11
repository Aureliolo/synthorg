---
description: All significant design and architecture decisions, organized by domain.
---

# Decision Log

All significant design and architecture decisions, organized by domain. Each entry is a brief summary — for detailed analysis (options considered, precedents, trade-offs), see the linked ADR.

## Memory

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| ADR-001 | Mem0 as initial memory backend behind pluggable `MemoryBackend` protocol | In-process, Python 3.14 compatible, Qdrant embedded + SQLite. Custom stack (Neo4j + Qdrant external) as future upgrade. Config-driven backend selection | [ADR-001](../decisions/ADR-001-memory-layer.md) |

## Security & Trust

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D1 | StrEnum + validated registry for action types; two-level `category:action` hierarchy; static tool metadata classification | Type safety + extensibility. Category shortcuts for simple config, fine-grained control when needed. No LLM in the security classification path | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d1-action-type-taxonomy) |
| D4 | Hybrid SecOps: rule engine fast path (~95%) + LLM slow path (~5%) | Rules catch known patterns (sub-ms, deterministic). LLM handles uncertain cases. Hard safety rules never bypass regardless of autonomy level | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d4-secops-llm-based-or-rule-based) |
| D5 | SecOps intercepts before every tool invocation via `SecurityInterceptionStrategy` protocol | Maximum coverage. Sub-ms rule check is invisible against seconds of LLM inference. Policy strictness (not interception point) varies by autonomy level | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d5-secops-integration-point-in-pipeline) |
| D6 | Three-level autonomy resolution: per-agent, per-department, company default | Matches real-world IAM systems. Seniority validation prevents Juniors from getting `full` autonomy | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d6-autonomy-per-agent-or-company-wide) |
| D7 | Human-only promotion + automatic downgrade via `AutonomyChangeStrategy` protocol | No real-world security system auto-grants higher privileges. Automatic downgrade on errors, budget exhaustion, or security incidents | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d7-autonomy-who-can-change-levels-at-runtime) |

## Agent & HR

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D8 | Templates + LLM for candidate generation; persist to operational store; hot-pluggable | Reuses template system for common roles, LLM for novel roles. Operational store enables rehiring and audit. Hot-plug via dedicated registry service | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d8-hr-runtime-agent-instantiation) |
| D9 | Pluggable `TaskReassignmentStrategy`; initial: queue-return | Tasks return to unassigned queue. Existing `TaskRoutingService` re-routes with priority boost for reassigned tasks | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d9-hr-task-reassignment-on-offboarding) |
| D10 | Pluggable `MemoryArchivalStrategy`; initial: full snapshot, read-only | Complete preservation. Selective promotion of semantic+procedural to org memory. Enables rehiring via archive restore | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d10-hr-memory-archival-semantics) |

## Performance Metrics

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D2 | Pluggable `QualityScoringStrategy`; initial: layered (CI signals + LLM judge + human override) | Multiple independent signals. Start with Layer 1 (free CI signals), add layers incrementally | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d2-quality-scoring-mechanism) |
| D3 | Pluggable `CollaborationScoringStrategy`; initial: automated behavioral telemetry | Objective, zero token cost. Weighted average of delegation success, response latency, conflict constructiveness, meeting contribution, loop prevention, handoff completeness | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d3-collaboration-scoring-mechanism) |
| D11 | Pluggable `MetricsWindowStrategy`; initial: multiple windows (7d, 30d, 90d) | Industry standard (Google SRE). Handles heterogeneous metric cadences. Min 5 data points per window | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d11-rolling-average-window) |
| D12 | Pluggable `TrendDetectionStrategy`; initial: Theil-Sen regression + thresholds | 29.3% outlier breakdown. Classifies trends as improving/stable/declining. Min 5 data points | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d12-trend-detection-approach) |

## Promotions

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D13 | Pluggable `PromotionCriteriaStrategy`; initial: configurable threshold gates (N of M) | `min_criteria_met` setting covers AND, OR, and threshold logic. Default: junior-to-mid = 2/3, mid-to-senior = all | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d13-promotion-criteria-logic-andor) |
| D14 | Pluggable `PromotionApprovalStrategy`; initial: senior+ requires human approval | Low-level auto-promotes (small cost impact). Demotions auto-apply for cost-saving, human approval for authority reduction | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d14-promotion-approval-requirements) |
| D15 | Pluggable `ModelMappingStrategy`; initial: default ON, opt-out | Model follows seniority. Changes at task boundaries only. Per-agent `preferred_model` overrides. Smart routing still uses cheap models for simple tasks | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d15-promotion-seniority-to-model-mapping) |

## Tools & Sandbox

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D16 | Docker MVP via `aiodocker`; `SandboxBackend` protocol for future backends | Docker cold start invisible against LLM latency. Pre-built image + user config. Fail if Docker unavailable. gVisor as config-level hardening upgrade | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d16-sandbox-backend-choice) |
| D17 | Official `mcp` Python SDK, pinned `>=1.25,<2`; `MCPBridgeTool` adapter | Used by every major framework. stdio + Streamable HTTP transports. Thin adapter isolates codebase from SDK changes | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d17-mcp-sdk-choice) |
| D18 | MCP result mapping via adapter in `MCPBridgeTool` | Keep `ToolResult` as-is. Text concatenation for LLM path. Rich content in metadata. Zero disruption to existing codebase | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d18-mcp-tool-result-mapping) |

## Timeout & Approval

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D19 | Pluggable `RiskTierClassifier`; initial: configurable YAML mapping | Predictable, hot-reloadable. Unknown action types default to HIGH (fail-safe) | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d19-timeout-risk-tier-classification-source) |
| D20 | Pydantic JSON via `PersistenceBackend`; `ParkedContextRepository` protocol | Pydantic handles serialization, SQLite handles durability. Conversation stored verbatim | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d20-timeout-context-serialization-format) |
| D21 | Tool result injection for approval resume | Approval IS the tool's return value. Satisfies LLM conversation protocol. Fallback: system message for engine-initiated parking | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d21-timeout-resume-injection) |

## Engine & Prompts

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| D22 | Remove tools section from system prompt | API's `tools` parameter injects richer definitions (with schemas). Eliminates 200-400+ token redundancy per call | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d22-tools-section-redundancy-in-system-prompt) |
| D23 | Pluggable `MemoryFilterStrategy`; initial: tag-based at write time | Zero retrieval cost. Uses existing `MemoryMetadata.tags`. Non-inferable tag convention enforced at `MemoryBackend.store()` boundary | [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md#d23-memory-filter-heuristic-non-inferable) |

## Documentation

| ID | Decision | Rationale | ADR |
|----|----------|-----------|-----|
| ADR-003 | MkDocs + Material + mkdocstrings for docs; Astro for landing page; build output embedding for Vue dashboard; single domain with CI merge | Best-in-class tools for each job. Griffe AST extraction (PEP 649 safe). Zero-JS landing page. Same docs in both locations | [ADR-003](../decisions/ADR-003-documentation-architecture.md) |

## Overarching Pattern

Nearly every decision follows the same architecture: a pluggable protocol interface with one initial implementation shipped, and alternative strategies documented for future extension. This is consistent with the project's protocol-driven design philosophy.

## ADR Files

The full ADR files contain detailed analysis — options considered, real-world precedents, trade-offs, and sub-decisions:

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-001](../decisions/ADR-001-memory-layer.md) | Memory Layer Selection | Accepted | 2026-03-08 |
| [ADR-002](../decisions/ADR-002-design-decisions-batch-1.md) | Design Decisions Batch 1 (D1-D23) | Accepted | 2026-03-09 |
| [ADR-003](../decisions/ADR-003-documentation-architecture.md) | Documentation & Site Architecture | Accepted | 2026-03-11 |
