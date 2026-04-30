---
description: All significant design and architecture decisions, organized by domain.
---

# Decision Log

All significant design and architecture decisions in force today, organized by domain. Each entry includes the decision, rationale, and key alternatives that were considered.

## Memory Layer

**Decision:** Mem0 as initial memory backend behind pluggable `MemoryBackend` protocol. Custom stack (Neo4j + Qdrant external) as planned future upgrade.

**Context:** 16+ agent memory solutions evaluated. After gate checks (local-first, license, Docker, Python 3.14+, per-agent isolation), three candidates passed: Mem0, Graphiti, and Custom Stack.

| Candidate | Score | Why chosen / rejected |
|-----------|-------|----------------------|
| **Mem0** (chosen) | 70/100 | Production-ready (v1.0+, 49k stars). In-process deployment (Qdrant embedded + SQLite). Python 3.14 compatible (`>=3.9,<4.0`). Async client available. Low adapter overhead (~500-1k lines). Known gap: flat fact model doesn't natively map to 5-type memory taxonomy (acceptable for initial backend) |
| Custom Stack | 80/100 | Best architectural fit but ~6-8k lines of custom code before any memory works. Deferred to future phase; build after Mem0 proves the protocol shape |
| Graphiti | 66/100 | Best temporal knowledge graph, but pre-1.0 stability (v0.28), extreme LLM ingestion costs (1000+ API calls per 10k chars), only covers 2-3 of 5 memory types |

**Eliminated:** Letta (Python `<3.14`), Cognee (Python `<3.14`), memU (AGPL-3.0), Supermemory (hosted API only), Graphlit (cloud-only). Both Letta and Cognee are on the watch list for when they add Python 3.14 support.

**Architecture:** Mem0 runs in-process inside the synthorg-backend Docker container. Qdrant embedded for vectors, SQLite for history, both persisting to mounted volumes. Graph memory (Neo4j) is optional, enabled via config. All behind the `MemoryBackend` protocol; swap backends via config without code changes.

## Security & Trust

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D1 | StrEnum + validated registry for action types; two-level `category:action` hierarchy; static tool metadata classification | Type safety + extensibility. Category shortcuts for simple config, fine-grained control when needed. No LLM in the security classification path | Closed enum (can't extend), open strings (typos = security hazard), LLM classification (non-deterministic, catastrophic for security). Precedents: AWS IAM, K8s RBAC, GitHub scopes |
| D4 | Hybrid SecOps: rule engine fast path (~95%) + LLM slow path (~5%) | Rules catch known patterns (sub-ms, deterministic). LLM handles uncertain cases. Hard safety rules never bypass regardless of autonomy level | Pure rules (can't handle novel situations), pure LLM (0.5-8.6s latency, non-deterministic, vulnerable to injection). Precedents: AWS GuardDuty, LlamaFirewall, NeMo Guardrails (all hybrid) |
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
| D3 | Pluggable `CollaborationScoringStrategy`; initial: automated behavioral telemetry + LLM calibration sampling (1%, opt-in) + human override via API | Objective, zero token cost for primary strategy. LLM sampling (1%) for drift calibration only, not full LLM evaluation. Human override via API for targeted corrections. Weighted average of delegation success, response latency, conflict constructiveness, meeting contribution, loop prevention, handoff completeness | Full LLM evaluation as primary strategy (expensive, circular: LLM judging LLM), peer ratings (reciprocity/collusion), human-provided as sole source (doesn't scale) |
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
| D16 | Docker MVP via `aiodocker`; `SandboxBackend` protocol for future backends | Docker cold start (1-2s) invisible against LLM latency (2-30s). Pre-built image + user config. Fail if Docker unavailable; no unsafe subprocess fallback. gVisor as config-level hardening upgrade | Docker + WASM (CPython can't run pip packages in WASM), Docker + Firecracker (Linux-only, requires KVM), docker-py (sync, no 3.14 support). Precedents: E2B, major cloud providers, Daytona (none offer unsandboxed fallback) |
| D17 | Official `mcp` Python SDK, exact-pinned (`==`), updated via Renovate; `MCPBridgeTool` adapter | Used by every major framework (LangChain, CrewAI, major agent SDKs, Pydantic AI). Python 3.14 compatible. Pydantic v2 compatible. Thin adapter isolates codebase from SDK changes | Custom MCP client (must implement protocol handshake, track spec changes manually) |
| D18 | MCP result mapping via adapter in `MCPBridgeTool` | Keep `ToolResult` as-is. Text concatenation for LLM path. Rich content in metadata. Zero disruption to existing codebase | Extend ToolResult for multi-modal (cascading changes across codebase; LLM providers consume as text anyway) |

## Timeout & Approval

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D19 | Pluggable `RiskTierClassifier`; initial: configurable YAML mapping | Predictable, hot-reloadable. Unknown action types default to HIGH (fail-safe) | Fixed per action type (rigid), SecOps assigns at runtime (non-deterministic, expensive), default + SecOps override (premature coupling). Precedent: OPA policy-as-config |
| D20 | Pydantic JSON via `PersistenceBackend`; `ParkedContextRepository` protocol | Pydantic handles serialization, SQLite handles durability. Conversation stored verbatim; summarization is a context window concern at resume time, not a persistence concern | Pydantic only (no durability), persistence only (still needs serialization format). Precedents: Temporal, LangGraph, SpiffWorkflow all store full state |
| D21 | Tool result injection for approval resume | Approval IS the tool's return value. Satisfies LLM conversation protocol (expects tool result after tool call). Fallback: system message for engine-initiated parking | System message (not for events, agent may not notice), context metadata flag (LLM doesn't see it). Precedent: LangGraph HITL pattern |

## Engine & Prompts

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D22 | Remove tools section from system prompt | API's `tools` parameter injects richer definitions (with JSON schemas). Eliminates 200-400+ token redundancy per call. Major LLM providers inject tool definitions internally | Keep as-is (wastes tokens, contradicts provider best practices), replace with behavioral guidance (requires per-tool-set crafting). Evidence: arXiv 2602.11988 shows redundant context increases cost 20%+ with minimal benefit |
| D23 | Pluggable `MemoryFilterStrategy`; initial: tag-based at write time | Zero retrieval cost. Uses existing `MemoryMetadata.tags`. Non-inferable tag convention enforced at `MemoryBackend.store()` boundary | LLM classification at retrieval (2K-10K extra tokens, adds latency, recursive problem), keyword heuristic (low accuracy), documentation only (no enforcement). Evidence: arXiv 2602.11988 confirms agents store inferable content without enforcement |
| D24 | Five-pillar evaluation: pluggable `PillarScoringStrategy` protocol with `EvaluationContext` bag; per-pillar configs with metric toggles | Single protocol covers all pillars. Context bag avoids per-pillar protocol proliferation. Per-metric toggles with weight redistribution follow `BehavioralTelemetryStrategy` pattern. Pull-based (no daemon) | Per-pillar protocols (5 protocols, type-safe but verbose), monolithic scorer (no pluggability), background evaluation loop (premature complexity). Based on [InfoQ five-pillar framework](https://www.infoq.com/articles/evaluating-ai-agents-lessons-learned/) |

## Documentation

**Decision:** Zensical + mkdocstrings for docs; Astro for landing page; build output embedding for React dashboard; single domain with CI merge.

**Rationale:** MkDocs has been unmaintained since August 2024. Material for MkDocs entered maintenance mode (v9.7.0 final, 12 months critical fixes only). Zensical is the designated successor by the same team (squidfunk), reads `mkdocs.yml` natively, and ships with the Material theme built-in. Griffe AST extraction for mkdocstrings remains PEP 649 safe. Zensical's `--strict` mode is not yet available ([zensical/backlog#72](https://github.com/zensical/backlog/issues/72)); CI builds without strict validation until that ships.

**Alternatives:** Stay on MkDocs (unmaintained, accumulating CVEs and unresolved issues), Sphinx (poor landing pages, different ecosystem), VitePress/Docusaurus (no Python API docs).

## Embedding Model Evaluation

**Decision:** Use [LMEB](https://arxiv.org/abs/2603.12572) (Long-horizon Memory Embedding Benchmark) instead of MTEB for evaluating and selecting embedding models for the memory subsystem.

**Context:** SynthOrg's memory retrieval spans episodic, procedural, semantic, and social categories: long-horizon, fragmented, context-dependent tasks. LMEB (Zhao et al., March 2026) evaluates exactly these patterns across 22 datasets and 193 tasks. Its key finding is that MTEB performance has near-zero or negative correlation with memory retrieval quality (overall Spearman: -0.130; dialogue: -0.364).

| Candidate | Score Basis | Why chosen / rejected |
|-----------|-------------|----------------------|
| **LMEB** (chosen) | 193 memory retrieval tasks across 4 types | Direct taxonomy mapping to SynthOrg's MemoryCategory enum. Evaluates the exact retrieval patterns the memory system uses |
| MTEB | General passage retrieval | MTEB performance does not transfer to memory retrieval (Pearson: -0.115). Optimizing for MTEB may actively harm memory retrieval quality |
| Manual evaluation | Custom retrieval benchmarks | Too expensive to maintain. LMEB provides a standardized, reproducible alternative |

**Model selection:** Three deployment tiers recommended based on LMEB scores. See [Embedding Evaluation](../reference/embedding-evaluation.md) for the full analysis. Domain-specific fine-tuning (+10-27% improvement) configured via `EmbeddingFineTuneConfig`; when enabled, the Mem0 adapter uses the checkpoint path as the model identifier. The five-stage offline pipeline (synthetic data generation, hard-negative mining, contrastive training, evaluation, deploy) is functional via `synthorg.memory.embedding.fine_tune`; orchestration ships in `synthorg.memory.embedding.fine_tune_orchestrator` and the admin endpoint `POST /admin/memory/fine-tune` drives it from the dashboard.

## Memory Architecture Evolution

| ID | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| D25 | Defer GraphRAG and Temporal KG; stay on Mem0 + Qdrant vector retrieval | GraphRAG adds entity extraction (LLM pass per document) + graph DB layer at 2-3x infrastructure cost and 200-400 ms vs 50-150 ms query latency. Current per-agent episodic/semantic memory use cases do not require multi-hop entity traversal. `MemoryBackend` protocol enables a drop-in `GraphRAGMemoryBackend` upgrade in Phase 2 without changing application code | Full GraphRAG migration (high cost, unclear benefit at current scale), Graphiti (pre-1.0 stability at evaluation time; see Memory Layer decision), Custom Stack (deferred, too early) |
| D26 | Adopt append-only writes + MVCC-style snapshot reads for `SharedKnowledgeStore`; personal memories stay sequential | Append-only provides audit trail ("what was the state before date X?"), rollback, and safe concurrent writes. MVCC snapshot reads are consistent with no locking overhead. Personal memories have no cross-agent contention so sequential writes are sufficient. Protocol extension (future PR): add `get_operation_log(fact_id)` and `snapshot_at(timestamp)` to `SharedKnowledgeStore` | CRDT (conflict-free but ~20% space overhead and resurfaces deleted facts on node divergence), event sourcing (good audit properties but requires snapshot compaction strategy), pessimistic locking (high contention under load, tail latency spikes) |
| D27 | RL consolidation not recommended for MVP; revisit at 10k+ agent deployments | Reward function is multi-objective (readability, retrieval accuracy, synthesis fidelity, token cost) and unsolved without ~1000 annotated sessions. Failure mode is data loss: RL model drift silently deletes memories; LLM degrades gracefully. At current scale (50-500 agents) training infra cost exceeds token savings by ~12 months. DPO fine-tuning on LLM preference data is the viable intermediate step if cost becomes a concern | Pure RL policy training (reward design is open research problem), behavioral cloning only (low gain over current LLM approach), threshold-based consolidation triggers (no quality improvement, only cost saving) |

## NATS Client Library

**Decision:** Stay on `nats-py` (pinned `==2.14.0`). File upstream PR to replace deprecated `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction`. Maintain scoped `filterwarnings` as workaround until upstream fix lands.

**Context:** PR #1214 (distributed runtime) introduced `nats-py==2.14.0` for the JetStream message bus and task queue. Python 3.14 CI fails because `nats-py` calls `asyncio.iscoroutinefunction` in `nats/aio/client.py:476`, deprecated in Python 3.14 and slated for removal in 3.16. Upstream (`nats-io/nats.py`) has no open issue or fix in progress; classifiers top out at Python 3.13. A separate library, `nats-core`, was evaluated as a potential replacement.

| Candidate | Why chosen / rejected |
|-----------|----------------------|
| **nats-py** (stay) | Only Python NATS client with JetStream support (streams, KV store, durable pull consumers, work-queue retention). Official `nats-io` project, Apache 2.0, asyncio-native. The `asyncio.iscoroutinefunction` deprecation is a one-line fix (`inspect.iscoroutinefunction` is a drop-in replacement, backward-compatible to Python 3.5+). All SynthOrg distributed features depend on JetStream primitives |
| nats-core v0.1.0 | Lean, zero-dependency client (63x faster for core ops). **Does not support JetStream, KV store, pull consumers, or durable consumers**: only core pub/sub, request/reply, and queue groups. Migration would require rewriting the entire message bus and task queue, losing persistence, durability, history, and KV-backed channel discovery. Also v0.1.0 with no API stability commitment |

**Eliminated:** No other Python NATS clients exist. Custom JetStream client over raw NATS protocol was not considered (substantial effort, no ecosystem benefit).

**SynthOrg JetStream usage** (verified in `bus/nats.py` facade and `workers/claim.py`): `SYNTHORG_BUS` stream (LimitsPolicy, `_nats_connection`), `SYNTHORG_TASKS` stream (WorkQueuePolicy, `claim.py`), `SYNTHORG_BUS_CHANNELS` KV bucket (`_nats_kv`), durable pull consumers with `ConsumerConfig` (`_nats_consumers`), stream management via `stream_info`/`add_stream`/`update_stream` (`_nats_connection`), history scanning with ephemeral consumers using `DeliverPolicy.ALL`/`AckPolicy.NONE` (`_nats_history`), connection lifecycle callbacks (`_nats_connection`).

**Mitigation plan:** (1) File upstream PR against `nats-io/nats.py` with the one-line `inspect.iscoroutinefunction` fix; upstream PR status is tracked in the project issue queue (search `nats-py` label); the scoped `filterwarnings` entry in `pyproject.toml` remains the active workaround until a fixed upstream release is available. (2) If upstream is unresponsive by **2026-06-10** (60 days from the 2026-04-11 review), maintain a local monkey-patch in `bus/_nats_compat.py`. (3) Monitor `nats-core` for future JetStream support.

## Overarching Pattern

Nearly every decision follows the same architecture: a pluggable protocol interface with one initial implementation shipped, and alternative strategies documented for future extension. This is consistent with the project's protocol-driven design philosophy.
