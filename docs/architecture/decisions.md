---
description: All significant design and architecture decisions, organized by domain.
---

# Decision Log

All significant design and architecture decisions in force today, organized by domain. Each entry includes the decision, rationale, and key alternatives that were considered.

## Memory Layer

**Decision:** Mem0 as initial memory backend behind pluggable `MemoryBackend` protocol. Custom stack (Neo4j + Qdrant external) as planned future upgrade.

**Context:** 16+ agent memory solutions evaluated. After gate checks (local-first, license, Docker, Python 3.14+, per-agent isolation), three candidates passed: Mem0, Graphiti, and Custom Stack. <!-- lint-allow: doc-numeric-macros -- frozen historical evaluation count, not a build-time stat -->

| Candidate | Score | Why chosen / rejected |
|-----------|-------|----------------------|
| **Mem0** (chosen) | 70/100 | Production-ready (v1.0+, <!--RS:mem0_stars-->56k+<!--/RS--> stars). In-process deployment (Qdrant embedded + SQLite). Python 3.14 compatible (`>=3.9,<4.0`). Async client available. Low adapter overhead (~500-1k lines). Known gap: flat fact model doesn't natively map to 5-type memory taxonomy (acceptable for initial backend) |
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
| D16 | Layered `SandboxBackend` protocol via `aiodocker`. Subprocess default for low-risk categories (file_system, git); Docker required for high-risk (code_execution, terminal, database, web) | Subprocess is genuinely safe for read-only / workspace-scoped categories: env filtering (allowlist + denylist), restricted PATH, workspace-scoped cwd, timeout + process-group kill, library-injection-var blocking. Docker is required where arbitrary code or network egress can land. Layered design preserves the local-first quickstart (file/git tools work without Docker) without weakening isolation where it matters. Docker cold start (1-2s) is invisible against LLM latency (2-30s). gVisor remains a config-level hardening upgrade for the Docker tier | Docker + WASM (CPython can't run pip packages in WASM), Docker + Firecracker (Linux-only, requires KVM), docker-py (sync, no 3.14 support), Docker-only for every category (rejected: forces a running container for trivial file reads, breaks local-first quickstart, no isolation gain over subprocess for read-only file_system tools). Precedents: E2B, major cloud providers, Daytona |
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
| D23 | Pluggable `MemoryFilterStrategy`; initial: tag-based at write time | Zero retrieval cost. Uses existing `MemoryMetadata.tags`. Non-inferable tag convention enforced at `MemoryBackend.store()` boundary | LLM classification at retrieval (2K-10K extra tokens, adds latency, recursive problem), keyword heuristic (low accuracy), documentation only (no enforcement). Evidence: arXiv 2602.11988 confirms agents store inferable content without enforcement <!-- lint-allow: doc-numeric-macros -- arXiv paper identifier, not a stat claim --> |
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
| D27 | RL consolidation not recommended for MVP; revisit at 10k+ agent deployments | Reward function is multi-objective (readability, retrieval accuracy, synthesis fidelity, token cost) and unsolved without ~1000 annotated sessions. Failure mode is data loss: RL model drift silently deletes memories; LLM degrades gracefully. At current scale (50-500 agents) training infra cost exceeds token savings by ~12 months. DPO fine-tuning on LLM preference data is the viable intermediate step if cost becomes a concern <!-- lint-allow: doc-numeric-macros -- illustrative deployment-scale thresholds (10k+, 50-500), not build-time stats --> | Pure RL policy training (reward design is open research problem), behavioral cloning only (low gain over current LLM approach), threshold-based consolidation triggers (no quality improvement, only cost saving) |

## NATS Client Library

> **Correction note:** the 2026-04-11 evaluation rejected `nats-core` on the basis that it "lacks JetStream, KV store, and durable consumers". That was a misread of the modular `nats-io/nats.py` client family (see caspervonb's [PR #1228 comment](https://github.com/Aureliolo/synthorg/pull/1228#issuecomment-4271799539) and issue [#2037](https://github.com/Aureliolo/synthorg/issues/2037)). The conclusion (stay on `nats-py`) holds; the reasoning below is the corrected evaluation.

**Decision:** Stay on `nats-py==2.14.0` with the scoped `filterwarnings` entry. Bump the pin to `nats-py==2.15.0` and drop the filter the moment that release ships (`nats-io/nats.py` PR [#932](https://github.com/nats-io/nats.py/pull/932), merged 2026-05-13, contains the `inspect.iscoroutinefunction` swap).

**Context:** `nats-py==2.14.0` backs the JetStream message bus and task queue (`SYNTHORG_BUS`, `SYNTHORG_TASKS`). Python 3.14 CI fails because `nats-py` calls `asyncio.iscoroutinefunction`, deprecated in 3.14 and slated for removal in 3.16. The fix has landed on `nats-py` main but has not yet been tagged for release: latest is still 2.14.0 (2026-02-23).

In parallel, the `nats-io/nats.py` repository is publishing a modular client family that mirrors the `nats.js` v3 split. The protocol layer (`nats-core`), the JetStream layer (`nats-jetstream`), and the KV layer (`nats-key-value`) are separate packages. The original ADR evaluated only the protocol layer in isolation and concluded the new family was missing JetStream/KV/consumers. It is not -- those surfaces live in the companion packages.

**Live package state (verified 2026-05-24 against PyPI JSON API and `nats-io/nats.py` main):**

| Package | Latest version | Released | Requires-Python | Dev status | Depends on |
|---|---|---|---|---|---|
| `nats-py` | 2.14.0 | 2026-02-23 | `>=3.7` | (none) | n/a |
| `nats-core` | 0.2.0 | 2026-04-14 | `>=3.13` | Beta | `nkeys>=0.1.0; extra=="nkeys"` |
| `nats-jetstream` | 0.3.0 | 2026-05-10 | `>=3.11` | Beta | `nats-core` (no version pin) |
| `nats-key-value` | 0.1.0 (workspace only) | 2026-05-08 | `>=3.13` | Beta | `nats-jetstream` (workspace ref) |
| `nats-kv` | -- (HTTP 404 on PyPI) | -- | -- | -- | -- |

`nats-key-value` lives in `nats-io/nats.py/nats-key-value/` and is wired into the workspace via `tool.uv.sources`; it is **not yet published to PyPI**. Adopting it today means a `git+https://github.com/nats-io/nats.py.git#subdirectory=nats-key-value` dependency, which breaks dependency-scanner ergonomics and offers no semver discipline.

| Candidate | Verdict | Reasoning |
|---|---|---|
| **`nats-py 2.14.0`** (chosen) | stay | Mature 2.x line, still maintained in parallel (PR #932 merged 2026-05-13). Single dependency, full feature coverage of every SynthOrg JetStream + KV use today, no API churn risk. Python 3.14 deprecation is bridged by a scoped `filterwarnings` entry until 2.15 ships. |
| `nats-core 0.2.0` + `nats-jetstream 0.3.0` + git-pinned `nats-key-value 0.1.0` | rejected for now | All three required surfaces (protocol, JetStream, KV) are technically available, so migration is viable. Cost: rewriting ~1700 LOC across nine `bus/` submodules; vendoring `nats-key-value` from a git subdirectory; three 0.x Beta dependencies with no API-stability commitment; confirmed regression on `publish_batch` (see API delta below). The benefits do not yet outweigh the cost while `nats-py` is healthy. |
| `nats-core 0.2.0` + `nats-jetstream 0.3.0` only (drop KV) | rejected | Drops the channel-discovery KV bucket (`SYNTHORG_BUS_CHANNELS`); would force a rewrite to a stream-based channel registry. Larger blast radius than the full migration for no net benefit. |
| Custom JetStream client over raw NATS protocol | rejected | Substantial effort, no ecosystem benefit. |

**SynthOrg NATS surface inventory** (verified in `src/synthorg/communication/bus/` and `src/synthorg/workers/claim.py`): `SYNTHORG_BUS` stream (LimitsPolicy, `_nats_connection`), `SYNTHORG_TASKS` stream (WorkQueuePolicy, `claim.py`), `SYNTHORG_BUS_CHANNELS` KV bucket (`_nats_kv`), durable pull consumers with `ConsumerConfig` (`_nats_consumers`), stream management via `stream_info`/`add_stream`/`update_stream` (`_nats_connection`), history scanning with ephemeral consumers using `DeliverPolicy.ALL` / `AckPolicy.NONE` (`_nats_history`), pipelined batch publish via `publish_async` + `publish_async_completed` (`_nats_publish`), connection lifecycle callbacks and `client.flush()` health probe (`_nats_connection`, `bus/nats.py`).

**API-surface delta (`nats-py` -> modular family), recorded for the future migration trigger:**

| Concern | `nats-py 2.14.0` | Modular family (`nats-core 0.2.0` / `nats-jetstream 0.3.0` / `nats-key-value 0.1.0`) | Notes |
|---|---|---|---|
| Connect + callbacks | `nats.connect(servers, disconnected_cb=, reconnected_cb=, error_cb=, ...)` | `nats.client.connect(...)`; callbacks attached via `client.add_disconnected_callback()` etc. (`nats-core/src/nats/client/__init__.py`) | Wiring shape change. |
| JetStream context | `client.jetstream()` | `nats.jetstream.new(client)` (`nats-jetstream/src/nats/jetstream/__init__.py`) | Module-level factory. |
| Stream lifecycle | `js.add_stream(StreamConfig(...))` / `js.update_stream(StreamConfig(...))` / `js.stream_info(name)` | `js.create_stream(StreamConfig(...))` / `js.update_stream(**config)` / `js.get_stream_info(name)` | Method renames + signature change on `update_stream`. |
| Durable pull consumer | `js.pull_subscribe(subject, durable, stream, config)` returning a `Subscription` | `js.create_or_update_consumer(stream_name, durable_name=, name=, **config)` returning a `Consumer` (async context manager) | Storage/lifetime pattern changes; `Consumer` must be entered with `async with`. |
| Consumer probe | `sub.consumer_info()` | `js.get_consumer_info(stream, consumer_name)` | Probe call moves from `sub` to `js`. |
| Consumer teardown | `sub.unsubscribe()` | `js.delete_consumer(stream, consumer_name)` | Teardown call moves from `sub` to `js`. |
| Synchronous publish | `js.publish(subject, payload, msg_ttl=)` | `js.publish(subject, payload, ttl=)` | Kwarg rename. |
| Pipelined batch publish | `js.publish_async(subject, payload)` + `js.publish_async_completed()` | **absent** from both `JetStream` (`nats-jetstream/src/nats/jetstream/__init__.py`) and `Stream` (`nats-jetstream/src/nats/jetstream/stream.py`) in 0.3.0 | Confirmed regression: a migrated `publish_batch` would lose native pipelining and fall back to `asyncio.gather` over per-message round-trips. Throughput penalty must be benchmarked at migration time. |
| Health probe | `client.flush(timeout=)` | `client.flush(timeout=None)` | Identical. |
| Drain | `client.drain()` | `client.drain(timeout=30.0)` | Identical. |
| Error hierarchy | `nats.errors.{NoServersError, TimeoutError, Error}`, `nats.js.errors.{NotFoundError, ...}` | `nats.client.errors`, `nats.jetstream.errors.{JetStreamError, MessageNotFoundError, StreamNotFoundError, StatusError}` | Hierarchy rename across every `except` clause in all nine `bus/` submodules. |

**KV surface delta** (`nats-py 2.14.0` `nats.js.KV` -> `nats-key-value 0.1.0` `nats.key_value.KeyValue`, source: `nats-io/nats.py/nats-key-value/src/nats/key_value/__init__.py` and `errors.py`):

| Concern | `nats-py 2.14.0` | `nats-key-value 0.1.0` | Notes |
|---|---|---|---|
| Bootstrap | `js.create_key_value(bucket=...)` / `js.key_value(name)` | `create_key_value(js, KeyValueConfig(bucket=...))` / `key_value(js, name)` | Module-level functions, not methods on `js`. |
| Atomic create-if-not-exists | `kv.create(key, value)` raises `KeyWrongLastSequenceError` on conflict | `KeyValue.create(key, value, ttl=None)` raises `KeyExistsError` | Cleaner semantics; every callsite of the rename must be updated. |
| Plain put / get | `kv.put(key, value)` / `kv.get(key)` returning entry with `.value` | `KeyValue.put(key, value)` / `KeyValue.get(key, revision=None)` returning `KeyValueEntry(.value, .revision, ...)` | Identical shape. |
| List keys | `await kv.keys()` returning `list[str]`; raises `NoKeysError` when empty | `await KeyValue.keys(">")` returning `KeyLister` (async iterator); empty case is an empty iterator, no `NoKeysError` | Caller-facing shape change: `async for k in await kv.keys():` instead of `for k in await kv.keys():`. |
| Error types caught today | `KeyNotFoundError`, `KeyWrongLastSequenceError`, `NoKeysError`, `BucketNotFoundError` (from `nats.js.errors`) | `KeyNotFoundError`, `KeyExistsError`, `BucketNotFoundError` (from `nats.key_value.errors`); `NoKeysError` does not exist | `NoKeysError` handler becomes dead code; `KeyWrongLastSequenceError` handler becomes `KeyExistsError`. |

**Trigger-based revisit** (replaces the prior fixed 2026-06-10 checkpoint):

1. **Trigger A -- `nats-py 2.15+` is released:** bump the pin in `pyproject.toml` to `nats-py==2.15.0`, drop the scoped `filterwarnings` entry, run `uv run python -m pytest tests/ -m integration -k nats` to confirm no deprecation warnings fire, and update this section with the resolution outcome. This closes the Python 3.14 thread without any further migration consideration.
2. **Trigger B -- `nats-key-value` is published to PyPI AND the modular family reaches 1.0:** re-evaluate migration. The API delta tables above are the starting scope; re-verify them at the published versions. If migration is decided, file the implementation as a separate issue, expand the scope with the `publish_batch` benchmark plan, and execute against PyPI-published packages only (no git subdirectory pins).
3. **Trigger C -- `nats-py` releases nothing for six months:** the parallel-maintenance assumption breaks. Re-evaluate migration urgency regardless of family stability.

**Calendar revisit: 2026-08-01.** `nats-py`'s release cadence is roughly quarterly and `nats-py 2.15` is overdue by then; if none of the triggers above have fired by that date, re-check upstream and refresh this section.

## Tooling & Developer Enforcement

**Decision:** Per-worktree git-hook isolation via a repo-committed,
venv-agnostic wrapper plus a *relative* `core.hooksPath`
(`scripts/git-hooks`). Hookify-style rules are enforced through
guaranteed-firing gates (`.claude/settings.json` PreToolUse for
tool-shaped rules, `.pre-commit-config.yaml` for code-content rules),
not declarative `.claude/hookify.*.md` files.

**Context:** All worktrees shared one `core.hooksPath`; pre-commit's
generated wrappers baked one worktree's venv into `INSTALL_PYTHON`, so
a venv change or worktree deletion broke every other worktree's
push/commit (observed on PR #1945). The in-repo `.claude/hookify.*.md`
rules had no dispatcher and were inert.

| Topic | Decision | Rationale | Alternatives considered |
|----|----------|-----------|------------------------|
| Hook isolation | Committed `scripts/git-hooks/{_run-hook.sh,pre-commit,pre-push,commit-msg}`; relative `core.hooksPath`; wrapper runs `uv run --frozen --project "$(git rev-parse --show-toplevel)" python -m pre_commit hook-impl ...`; `UV_FROZEN=1` exported | Git resolves a relative `core.hooksPath` from each worktree's working-tree root (verified on Git-for-Windows + linked worktrees), so each worktree runs its own wrapper against its own venv with zero per-worktree setup; deletion-safe by construction; removes the hardcoded-path failure class entirely | `extensions.worktreeConfig` + per-worktree `pre-commit install` (rejected: repo-wide flag flip, keeps the baked path, depends on never forgetting the install step) |
| Hookify enforcement | Migrate important rules to script gates, delete the 9 inert `.md` | Matches the repo's proven settings.json + `scripts/check_*` pattern; deterministic blocking; no new framework duplicating the external hookify plugin | In-repo hookify dispatcher (rejected: duplicates installed plugin; rules were warn-only) |
| `pytest-unit` "files were modified" | `UV_FROZEN`/`--frozen` in the wrapper (covers every inner `uv run` hook) + a pre/post `git status` reconcile guard in `scripts/run_affected_tests.py` that reverts only run-induced tracked changes | Leading cause is `uv run` rewriting `uv.lock` on stale lock / parallel-worktree race; `--frozen` removes it structurally, the guard is a root-cause-independent backstop that never silently passes | Script-only restore without `UV_FROZEN` (rejected: leaves the churn source in place) |
| `long-running-loops` failure | No code-loop change; root cause was collateral of the shared-hooks band-aid + `uv.lock` churn (gate is read-only and passes cleanly on the rebased branch). Structurally fixed by the per-worktree venv + `UV_FROZEN`; interpreter invariant pinned by `tests/unit/scripts/test_git_hooks_wrapper.py` | The gate cannot itself trip "files modified"; destabilising the whole pre-push run did | Treating it as an independent gate bug (rejected: no evidence; gate green with zero loop edits) |
| `pep758-except`, `function-length` | Advisory only; `.md` deleted, NO hard gate | 902 pre-existing `except (A, B):` sites means the `except A, B:` style is not actually practiced; a hard gate (or 902-entry baseline) is wrong and far outside scope. `function-length` ("<50 lines") is proxied by ruff `PLR0915`. Both were warn-only/inert | Hard gate + mass baseline (rejected: buries signal, scope explosion) |
| `enforce-parallel-tests` semantics | Block any explicit non-zero `-n`/`--numprocesses`; block xdist-disable (`-n0`/`--dist no`/`-p no:xdist`) unless a single `path::test` node id is present; benchmarks/`--codspeed` exempt | The literal hookify rule ("must contain `-n 8`") would have blocked the documented `pytest tests/ -m unit` (pyproject `addopts` already pins `-n=8 --dist=loadfile`). The only correct form is no `-n` flag; single-process is valid solely to read one test's full log | Faithful port of the inert rule (rejected: workflow-breaking bug) |
| Bulk-edit guard scope | `scripts/check_no_bulk_edit.py` blocks only shell in-place rewrites (`sed -i`, `perl -pi`, redirect-overwrite); native `Edit`/`Write` (incl. `replace_all`) allowed | User decision after weighing the `replace_all` empty-`new_string` newline-collapse footgun: the atomic reviewable diff is the safeguard; shell forms surface no diff | Block all `Edit replace_all` (rejected by user); in-repo dispatcher (n/a) |

MSW worker drift (`web/public/mockServiceWorker.js`) is handled
structurally (option C): the codegen file is gitignored and
regenerated by a guarded `web` `postinstall`, removing Renovate from
the loop. The complementary CI drift-guard is owned separately
(#1938).

## Overarching Pattern

Nearly every decision follows the same architecture: a pluggable protocol interface with one initial implementation shipped, and alternative strategies documented for future extension. This is consistent with the project's protocol-driven design philosophy.
