---
description: All significant design and architecture decisions, organized by domain.
---

# Decision Log

All significant design and architecture decisions, organized by domain. Each entry includes the decision, rationale, and key alternatives that were considered.

## Memory Layer (2026-03-08)

**Decision:** Mem0 as initial memory backend behind pluggable `MemoryBackend` protocol. Custom stack (Neo4j + Qdrant external) as planned future upgrade.

**Context:** 16+ agent memory solutions evaluated. After gate checks (local-first, license, Docker, Python 3.14+, per-agent isolation), three candidates passed: Mem0, Graphiti, and Custom Stack.

| Candidate | Score | Why chosen / rejected |
|-----------|-------|----------------------|
| **Mem0** (chosen) | 70/100 | Production-ready (v1.0+, 49k stars). In-process deployment (Qdrant embedded + SQLite). Python 3.14 compatible (`>=3.9,<4.0`). Async client available. Low adapter overhead (~500-1k lines). Known gap: flat fact model doesn't natively map to 5-type memory taxonomy — acceptable for initial backend |
| Custom Stack | 80/100 | Best architectural fit but ~6-8k lines of custom code before any memory works. Deferred to future phase — build after Mem0 proves the protocol shape |
| Graphiti | 66/100 | Best temporal knowledge graph, but pre-1.0 stability (v0.28), extreme LLM ingestion costs (1000+ API calls per 10k chars), only covers 2-3 of 5 memory types |

**Eliminated:** Letta (Python `<3.14`), Cognee (Python `<3.14`), memU (AGPL-3.0), Supermemory (hosted API only), Graphlit (cloud-only). Both Letta and Cognee are on the watch list for when they add Python 3.14 support.

**Architecture:** Mem0 runs in-process inside the synthorg-backend Docker container. Qdrant embedded for vectors, SQLite for history, both persisting to mounted volumes. Graph memory (Neo4j) is optional, enabled via config. All behind the `MemoryBackend` protocol — swap backends via config without code changes.

## Security & Trust

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D1 | StrEnum + validated registry for action types; two-level `category:action` hierarchy; static tool metadata classification | Type safety + extensibility. Category shortcuts for simple config, fine-grained control when needed. No LLM in the security classification path | Closed enum (can't extend), open strings (typos = security hazard), LLM classification (non-deterministic, catastrophic for security). Precedents: AWS IAM, K8s RBAC, GitHub scopes |
| D4 | Hybrid SecOps: rule engine fast path (~95%) + LLM slow path (~5%) | Rules catch known patterns (sub-ms, deterministic). LLM handles uncertain cases. Hard safety rules never bypass regardless of autonomy level | Pure rules (can't handle novel situations), pure LLM (0.5-8.6s latency, non-deterministic, vulnerable to injection). Precedents: AWS GuardDuty, LlamaFirewall, NeMo Guardrails — all hybrid |
| D5 | SecOps intercepts before every tool invocation via `SecurityInterceptionStrategy` protocol | Maximum coverage. Sub-ms rule check is invisible against seconds of LLM inference. Policy strictness (not interception point) varies by autonomy level | Before task step (misses per-tool threats), before task assignment only (zero runtime security), configurable per autonomy (the point doesn't change, only policy does) |
| D6 | Three-level autonomy resolution: per-agent, per-department, company default | Matches real-world IAM systems (AWS, Azure, K8s). Seniority validation prevents Juniors from getting `full` autonomy | Company-wide only (too coarse), per-department (can't distinguish junior from lead). Precedents: CrewAI per-agent attributes, AutoGen per-agent `human_input_mode` |
| D7 | Human-only promotion + automatic downgrade via `AutonomyChangeStrategy` protocol | No real-world security system auto-grants higher privileges. Automatic downgrade on errors, budget exhaustion, or security incidents | Human only (too restrictive for downgrades), CEO agent can promote (prompt injection risk → privilege escalation), fully automatic (dangerous). Precedent: Azure Conditional Access only restricts, never loosens |

## Agent & HR

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D8 | Templates + LLM for candidate generation; persist to operational store; hot-pluggable | Reuses template system for common roles, LLM for novel roles. Operational store enables rehiring and audit. Hot-plug via dedicated registry service | Templates only (can't create novel roles), LLM only (risk of invalid configs), in-memory only (lost on restart), persist to YAML (race conditions). Precedents: AutoGen hot-pluggable, Letta DB-persisted |
| D9 | Pluggable `TaskReassignmentStrategy`; initial: queue-return | Tasks return to unassigned queue. Existing `TaskRoutingService` re-routes with priority boost for reassigned tasks | Same-department/lowest-load (ignores skill match), manager decides (LLM cost, blocks on availability), HR agent decides (expensive, bottleneck) |
| D10 | Pluggable `MemoryArchivalStrategy`; initial: full snapshot, read-only | Complete preservation. Selective promotion of semantic+procedural to org memory. Enables rehiring via archive restore | Full snapshot accessible (exposes personal reasoning), selective discard (irrecoverable if classification wrong) |

## Performance Metrics

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D2 | Pluggable `QualityScoringStrategy`; initial: layered (CI signals + LLM judge + human override) | Multiple independent signals, hardest to game. Start with Layer 1 (free CI signals), add layers incrementally | Human only (doesn't scale), LLM-as-judge only (12+ known biases), CI signals only (narrow view), peer ratings (reciprocity bias). Research: LLM judges >80% human alignment but biased (CALM framework) |
| D3 | Pluggable `CollaborationScoringStrategy`; initial: automated behavioral telemetry | Objective, zero token cost. Weighted average of delegation success, response latency, conflict constructiveness, meeting contribution, loop prevention, handoff completeness | LLM evaluation (expensive, circular — LLM judging LLM), peer ratings (reciprocity/collusion), human-provided (doesn't scale) |
| D11 | Pluggable `MetricsWindowStrategy`; initial: multiple windows (7d, 30d, 90d) | Industry standard (Google SRE Workbook prescribes multi-window alerting). Handles heterogeneous metric cadences. Min 5 data points per window | Fixed 30d (too rigid), configurable per-metric (added complexity without multi-resolution benefit) |
| D12 | Pluggable `TrendDetectionStrategy`; initial: Theil-Sen regression + thresholds | 29.3% outlier breakdown (tolerates ~1 in 3 bad data points). Classifies trends as improving/stable/declining. Min 5 data points | Period-over-period (statistically weak), OLS regression (0% outlier breakdown), threshold-only (not a trend detection method). EPA recommends Theil-Sen for noisy data |

## Promotions

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D13 | Pluggable `PromotionCriteriaStrategy`; initial: configurable threshold gates (N of M) | `min_criteria_met` setting covers AND, OR, and threshold logic. Default: junior-to-mid = 2/3, mid-to-senior = all | AND only (blocks strong agents with one weak metric), OR only (trivial task spam → auto-promote). Precedents: game progression systems, HR competency matrices |
| D14 | Pluggable `PromotionApprovalStrategy`; initial: senior+ requires human approval | Low-level auto-promotes (small cost impact: small→medium ~4x). Demotions auto-apply for cost-saving, human approval for authority reduction | All human-approved (bottleneck on mass promotions), configurable per-level (extra complexity without clear benefit) |
| D15 | Pluggable `ModelMappingStrategy`; initial: default ON, opt-out | Model follows seniority. Changes at task boundaries only. Per-agent `preferred_model` overrides. Smart routing still uses cheap models for simple tasks | Always applied (budget-constrained deployments can't promote without cost increase), opt-in only (seniority feels disconnected from capability) |

## Tools & Sandbox

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D16 | Docker MVP via `aiodocker`; `SandboxBackend` protocol for future backends | Docker cold start (1-2s) invisible against LLM latency (2-30s). Pre-built image + user config. Fail if Docker unavailable — no unsafe subprocess fallback. gVisor as config-level hardening upgrade | Docker + WASM (CPython can't run pip packages in WASM), Docker + Firecracker (Linux-only, requires KVM), docker-py (sync, no 3.14 support). Precedents: E2B, OpenAI, Daytona — none offer unsandboxed fallback |
| D17 | Official `mcp` Python SDK, pinned `>=1.25,<2`; `MCPBridgeTool` adapter | Used by every major framework (LangChain, CrewAI, OpenAI Agents, Pydantic AI). Python 3.14 compatible. Pydantic 2.12.5 compatible. Thin adapter isolates codebase from SDK changes | Custom MCP client (must implement protocol handshake, track spec changes manually) |
| D18 | MCP result mapping via adapter in `MCPBridgeTool` | Keep `ToolResult` as-is. Text concatenation for LLM path. Rich content in metadata. Zero disruption to existing codebase | Extend ToolResult for multi-modal (cascading changes across codebase; LLM providers consume as text anyway) |

## Timeout & Approval

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D19 | Pluggable `RiskTierClassifier`; initial: configurable YAML mapping | Predictable, hot-reloadable. Unknown action types default to HIGH (fail-safe) | Fixed per action type (rigid), SecOps assigns at runtime (non-deterministic, expensive), default + SecOps override (premature coupling). Precedent: OPA policy-as-config |
| D20 | Pydantic JSON via `PersistenceBackend`; `ParkedContextRepository` protocol | Pydantic handles serialization, SQLite handles durability. Conversation stored verbatim — summarization is a context window concern at resume time, not a persistence concern | Pydantic only (no durability), persistence only (still needs serialization format). Precedents: Temporal, LangGraph, SpiffWorkflow all store full state |
| D21 | Tool result injection for approval resume | Approval IS the tool's return value. Satisfies LLM conversation protocol (expects tool result after tool call). Fallback: system message for engine-initiated parking | System message (not for events, agent may not notice), context metadata flag (LLM doesn't see it). Precedent: LangGraph HITL pattern |

## Engine & Prompts

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D22 | Remove tools section from system prompt | API's `tools` parameter injects richer definitions (with JSON schemas). Eliminates 200-400+ token redundancy per call. Both Anthropic and OpenAI inject tool definitions internally | Keep as-is (wastes tokens, contradicts provider best practices), replace with behavioral guidance (requires per-tool-set crafting). Evidence: arXiv 2602.11988 shows redundant context increases cost 20%+ with minimal benefit |
| D23 | Pluggable `MemoryFilterStrategy`; initial: tag-based at write time | Zero retrieval cost. Uses existing `MemoryMetadata.tags`. Non-inferable tag convention enforced at `MemoryBackend.store()` boundary | LLM classification at retrieval (2K-10K extra tokens, adds latency, recursive problem), keyword heuristic (low accuracy), documentation only (no enforcement). Evidence: arXiv 2602.11988 confirms agents store inferable content without enforcement |

## Documentation (2026-03-12)

**Decision:** Zensical + mkdocstrings for docs; Astro for landing page; build output embedding for Vue dashboard; single domain with CI merge.

**Rationale:** MkDocs has been unmaintained since August 2024. Material for MkDocs entered maintenance mode (v9.7.0 final, 12 months critical fixes only). Zensical is the designated successor by the same team (squidfunk), reads `mkdocs.yml` natively, and ships with the Material theme built-in. Griffe AST extraction for mkdocstrings remains PEP 649 safe. Zensical's `--strict` mode is not yet available ([zensical/backlog#72](https://github.com/zensical/backlog/issues/72)) — CI builds without strict validation until that ships.

**Alternatives:** Stay on MkDocs (unmaintained, accumulating CVEs and unresolved issues), Sphinx (poor landing pages, different ecosystem), VitePress/Docusaurus (no Python API docs).

## Overarching Pattern

Nearly every decision follows the same architecture: a pluggable protocol interface with one initial implementation shipped, and alternative strategies documented for future extension. This is consistent with the project's protocol-driven design philosophy.
