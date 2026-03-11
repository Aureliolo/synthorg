# ADR-001: Memory Layer Selection

## Status

Accepted

## Date

2026-03-08

## Context

The `memory/` module in `DESIGN_SPEC.md` (sections 7.1-7.4, 15.2) lists the memory
layer as "TBD — candidates: Mem0, Zep, Letta, Cognee, custom." (Note: Zep pivoted to
**Graphiti** as their open-source temporal knowledge graph offering; the standalone Zep
product is now a cloud-only service. This evaluation covers Graphiti as Zep's
successor.) This decision blocks
the memory subsystem implementation:

- **#32** Memory interface design
- **#36** Persistence layer
- **#41** Retrieval and injection
- **#125** Shared organizational memory
- **#48** Consolidation

### Key Architecture Constraints (from user)

1. **Target architecture**: memory/storage runs in **separate container(s)** from the
   main Python app. **MVP exception**: an in-process deployment (e.g., Mem0 inside the
   `synthorg-backend` container) is acceptable as long as it preserves the same protocol
   boundary and can be moved out-of-process without refactors.
2. Does NOT have to be Python — any technology, containerized
3. Main app uses a **thin async Python client** behind a **pluggable protocol**, which
   must work for both in-process libraries and remote services so storage can
   transparently move to separate container(s) later.
4. **Capability discovery** — protocol exposes what each backend supports
5. Multiple containers are fine (e.g., graph DB + vector store)
6. **Graph DB**: both Neo4j (server) and embedded options should be evaluated
7. **Embeddings**: implementation detail of the memory layer — just verify
   configurable providers (local + cloud)

### Requirements from Design Spec

- **5 memory types**: Working, Episodic, Semantic, Procedural, Social (§7.2)
- **4 persistence levels**: none, session, project, full (§7.3)
- **Per-agent isolation**: namespace/tenant support
- **3 org memory backends** behind `OrgMemoryBackend` protocol (§7.4):
  - Backend 1: Hybrid Prompt + Retrieval (MVP)
  - Backend 2: GraphRAG Knowledge Graph (research)
  - Backend 3: Temporal Knowledge Graph (research)
- **Python 3.14+** compatibility (project requirement)

---

## Discovery Phase: Candidates Found

An exhaustive search (web, GitHub, community forums, awesome-lists) identified **16+
agent memory solutions** as of March 2026. The field is expanding rapidly — most
projects launched or matured significantly in 2025.

### Long List

| # | Candidate | Stars | License | Graph | Vector | Temporal | Local |
|---|-----------|-------|---------|-------|--------|----------|-------|
| 1 | Mem0 | ~49k | Apache 2.0 | Yes (optional) | Yes | Basic | Yes |
| 2 | Supermemory | ~33k | MIT (SDK only) | No | Yes | No | No (proprietary engine) |
| 3 | Graphiti (Zep) | ~23k | Apache 2.0 | Yes (primary) | Yes | Bi-temporal | Yes |
| 4 | Letta (MemGPT) | ~21.5k | Apache 2.0 | No | Yes | No | Yes |
| 5 | memU | ~10.5k | AGPL-3.0 | No | Yes | No | Yes |
| 6 | Cognee | ~8.2k | Apache 2.0 | Yes | Yes | Partial | Yes |
| 7 | MemOS | ~5.9k | Apache 2.0 | Yes | Yes | Yes | Yes |
| 8 | Memori (Gibson) | ~4.9k | Apache 2.0 | No (SQL) | No | No | Yes |
| 9 | MemMachine | ~4.6k | Apache 2.0 | No | Yes | No | Yes |
| 10 | OpenMemory | ~3.5k | MIT | Yes | Yes | Yes | Yes |
| 11 | Memary | ~2.5k | MIT | Yes | Yes | No | Partial |
| 12 | LangMem | ~1.3k | MIT | No | Yes | No | Yes |
| 13 | SimpleMem | ~1.3k | MIT | No | Yes | No | Yes |
| 14 | A-MEM | ~835 | MIT | Yes | Yes | No | Yes |
| 15 | memsearch | ~227 | MIT | No | Yes | No | Yes |
| 16 | Graphlit | N/A | Cloud-only | Yes | Yes | No | No |
| -- | Custom Stack | -- | -- | Full control | Full control | Full control | Yes |

---

## Gate Check Results

### Gate Definitions

| Gate | Requirement | Method |
|------|-------------|--------|
| G1 | Runs fully local (no mandatory cloud) | Docs review, offline deploy test |
| G2 | License compatible with BUSL-1.1 | LICENSE file review |
| G3 | Containerizable as Docker service | Dockerfile/compose review |
| G4 | Active maintenance (release in last 6 months) | GitHub releases, commits |
| G5 | Per-agent memory isolation | API docs review |
| G6 | Configurable embedding provider (local + cloud) | Docs review |
| G7 | **Python 3.14+ compatible** | PyPI `requires-python` review |

### Gate Results

| Candidate | G1 | G2 | G3 | G4 | G5 | G6 | G7 | Result |
|-----------|----|----|----|----|----|----|----|----|
| **Mem0** | PASS | PASS (Apache 2.0) | PASS (in-process or 3 containers) | PASS (v1.0.5, Mar 2026) | PASS (user/agent/app/run_id) | PASS (11+ providers) | PASS (`>=3.9,<4.0`) | **PASS** |
| **Graphiti** | PASS | PASS (Apache 2.0) | PASS (compose) | PASS (v0.28.1, Feb 2026) | PASS (group_id) | PASS (4 providers) | PASS (`>=3.10,<4`) | **PASS** |
| **Letta** | PASS | PASS (Apache 2.0) | PASS | PASS (v0.16.6) | PASS (inherent) | PASS | **FAIL (`<3.14`)** | **ELIMINATED** |
| **Cognee** | PASS | PASS (Apache 2.0) | PASS (compose) | PASS (v0.5.3) | PASS (multi-tenant) | PASS (8+) | **FAIL (`<3.14`)** | **ELIMINATED** |
| **memU** | PASS | **FAIL (AGPL-3.0)** | -- | -- | -- | -- | -- | **ELIMINATED** |
| **Supermemory** | **FAIL (hosted API)** | -- | -- | -- | -- | -- | -- | **ELIMINATED** |
| **Graphlit** | **FAIL (cloud-only)** | -- | -- | -- | -- | -- | -- | **ELIMINATED** |
| **MemOS** | PASS | PASS (Apache 2.0) | PASS | PASS | Unclear | PASS | PASS (`>=3.10`) | Viable but immature |
| **Custom Stack** | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |

### Gate Elimination Details

- **Letta**: `requires-python = "<3.14,>=3.11"`. Conservative upper bound (no known
  technical blocker), but no upstream issue/PR requesting 3.14 support. Also: Letta is
  a full agent platform, NOT a standalone memory layer — the memory component cannot be
  used independently.

- **Cognee**: `requires-python = ">=3.10,<3.14"`. Conservative bound. Latest dev
  release (0.5.4.dev1, 2026-03-05) still has `<3.14`. Kuzu dependency itself supports
  3.14, so the constraint is from Cognee's own build/test matrix. Also early-stage
  maturity (v0.5.x).

- **memU**: AGPL-3.0 is copyleft and incompatible with BUSL-1.1 project licensing
  without careful isolation.

- **Supermemory**: The MIT-licensed repo contains only client SDKs and a web console
  — zero engine code. The actual memory engine (fact extraction, contradiction
  resolution, vector search) is proprietary. "Self-hosting" exists only for enterprise
  customers deploying a compiled proprietary bundle to Cloudflare Workers — not Docker.
  No Dockerfile exists in the repo.

- **Graphlit**: Cloud-native only by design. No self-hosting option.

### Candidates Passing All Gates

1. **Mem0** (Apache 2.0, `>=3.9,<4.0`, 49k stars)
2. **Graphiti** (Apache 2.0, `>=3.10,<4`, 23k stars)
3. **Custom Stack** (full control, all components Python 3.14 verified)

---

## Scored Evaluation

### Scoring Criteria (100 points)

| # | Criterion | Weight | Description |
|---|-----------|--------|-------------|
| S1 | Memory type coverage | 15 | How naturally 5 types map to candidate abstractions |
| S2 | Retrieval quality reputation | 15 | Benchmarks, reviews, user reports |
| S3 | Graph/relational capability | 12 | GraphRAG, temporal KG support (§7.4) |
| S4 | Stability and maturity | 12 | Version history, production usage, breaking changes |
| S5 | Protocol compatibility | 10 | Impedance with our `@runtime_checkable` protocol pattern |
| S6 | Operational persistence overlap | 8 | Can store tasks/costs/messages too? (#36) |
| S7 | Async support | 8 | Native async client quality |
| S8 | Consolidation built-in | 5 | Memory compression/summarization (#48) |
| S9 | Community, docs, ecosystem | 5 | Stars, contributors, doc quality |
| S10 | Resource footprint | 5 | RAM/disk/CPU requirements |
| S11 | Operational complexity | 5 | Container count, config, maintenance |

### Comparison Table

| Criterion | Mem0 | Graphiti | Custom Stack |
|-----------|------|---------|-------------|
| **S1** Memory types (15) | **9** — episodic+semantic+procedural+short-term. No explicit social/working. Flat fact model needs wrapping | **7** — episodic+semantic+social via graph. No procedural/working | **15** — full control, maps directly to all 5 types |
| **S2** Retrieval quality (15) | **12** — +26% vs. OpenAI Memory on LOCOMO. Well benchmarked | **11** — +18.5% accuracy, 90% latency reduction. Graph traversal powerful | **10** — depends on implementation. Qdrant + Neo4j individually excellent |
| **S3** Graph capability (12) | **8** — graph is supplementary to vector. Neo4j/Kuzu/FalkorDB. Enriches results but doesn't drive retrieval | **12** — graph IS the primary store. Bi-temporal model. Neo4j/FalkorDB/Kuzu | **11** — Neo4j is best-in-class. Full Cypher. Must implement temporal tracking |
| **S4** Stability (12) | **11** — v1.0+, 49k stars, YC-backed. v1.0.0 had breaking changes but migration guide exists | **7** — pre-1.0 (v0.28), fast-moving API. Docker image freshness issues. Hallucination bugs reported | **10** — each component individually very mature. No unified project risk |
| **S5** Protocol compat (10) | **6** — factory-based, opinionated memory structure. Needs adapter layer that fights its own abstractions | **7** — GraphDriver ABC is protocol-like. Async-native. Cleaner wrapping than Mem0 | **10** — built from scratch to match our protocol pattern exactly |
| **S6** Persistence overlap (8) | **3** — memory-focused only. No tasks/costs/messages | **2** — knowledge graph only | **5** — can add SQLite/Postgres for operational data naturally |
| **S7** Async support (8) | **6** — AsyncMemory added after community request (#2495). Works but secondary path | **8** — fully async throughout. Native design | **8** — Neo4j async driver + Qdrant async client both confirmed |
| **S8** Consolidation (5) | **4** — built-in memory compression engine | **3** — community detection, entity deduplication | **1** — must implement from scratch |
| **S9** Community/docs (5) | **5** — largest community (49k stars), good docs, YC backing | **4** — 23k stars, growing fast, good docs | **3** — components have great docs but no unified project |
| **S10** Resource footprint (5) | **3** — full graph stack: 3 containers (FastAPI + PostgreSQL + Neo4j); in-process mode lighter (Qdrant embedded + SQLite) | **3** — graph DB container + heavy LLM usage during ingestion | **3** — 2 containers (Neo4j + Qdrant) + embedded FastEmbed |
| **S11** Operational complexity (5) | **3** — full graph stack: 3 containers, OpenAI defaults need reconfiguration for local; in-process mode simpler | **2** — graph DB + high LLM cost per episode ingestion (1000+ API calls per 10k chars reported) | **4** — 2 well-understood containers, standard config |
| | | | |
| **TOTAL** | **70/100** | **66/100** | **80/100** |

### Analysis

**Mem0 (70/100)** — Most mature and well-adopted. Best retrieval benchmarks.
However, its flat "memory as facts" model does not naturally map to our 5-type
taxonomy. Graph memory is optional and supplementary, not primary. Would need a
significant adapter layer that fights Mem0's opinionated architecture. Note: graph DB
is entirely optional (disabled by default) and supports Neo4j, FalkorDB, Memgraph,
and Kuzu as backends — Kuzu-specific concurrency bugs do not apply when using other
graph backends.

**Graphiti (66/100)** — Best temporal knowledge graph capabilities, which maps
perfectly to §7.4 Backend 3. However, only covers 2-3 of 5 memory types (no
procedural, no working memory). Pre-1.0 stability concerns. The biggest risk is **LLM
ingestion cost** — users reported 1,000+ API requests for 10k chars and 24,000 API
calls / 41M tokens for processing a documentation set. This conflicts with our
cost-aware design principles.

**Custom Stack (80/100)** — Best architectural fit. Perfect protocol compatibility.
Full control over 5-type memory model. Python 3.14 verified per-component. Main
trade-off: ~6,000-8,000 lines of custom code (plus ~2,500-3,000 lines of tests) and
no built-in consolidation or memory extraction. However, we need the protocol layer
regardless, and the extraction logic can leverage our existing LiteLLM provider layer.

---

## Decision

### Initial Backend: Mem0 (in-process, persistent)

**Mem0** as the initial `MemoryBackend` implementation — get working memory fast,
build a proper custom backend later.

| Component | Technology | License | Role |
|-----------|-----------|---------|------|
| **Memory engine** | Mem0 (`mem0ai`) | Apache 2.0 | Memory extraction, storage, retrieval |
| **Vector store** | Qdrant (embedded, in-process) | Apache 2.0 | Persists to configurable path on mounted volume |
| **History store** | SQLite (in-process) | Public domain | Memory history, persists to configurable path |
| **Embeddings** | Configurable (FastEmbed for local, LiteLLM for cloud) | Apache 2.0 / MIT | Mem0 supports 11+ embedding providers |
| **Graph memory** | Optional (Neo4j when needed) | Apache 2.0 (driver) | Enable via config when graph capabilities needed |
| **Working memory** | In-process Python | N/A | Ephemeral per-task context |

### Why Mem0 as Initial

1. **Production-ready now**: v1.0+, 49k stars, YC-backed. `pip install mem0ai` and go.
2. **In-process deployment**: Qdrant embedded + SQLite — runs inside the synthorg-backend
   Docker container. No external services needed. Persists to mounted volumes.
3. **Python 3.14 compatible**: `>=3.9,<4.0`.
4. **Configurable everything**: embedding provider, vector store, graph store, LLM
   provider, storage paths — all via config dict.
5. **Async client available**: `AsyncMemory` class with full method parity.
6. **Graph is optional**: Start without graph, enable via config flag when needed.
7. **Low adapter overhead**: Thin wrapper (~500-1k lines) behind our protocol.

### What Mem0 Does NOT Cover (known gaps, accepted for now)

- **5-type taxonomy**: Mem0 treats memories as flat facts. No native distinction
  between episodic/semantic/procedural/social. Adapter maps memory types via metadata
  tags — imperfect but functional.
- **Social memory**: No graph-native relationship modeling (unless graph is enabled).
- **Consolidation control**: Mem0 has built-in compression but limited fine-tuning.
- **Full temporal model**: Basic timestamps, not bi-temporal tracking.

These gaps are accepted for the initial backend. The protocol architecture ensures
they can be addressed by a future custom backend without any consumer-side changes.

### Future Backend: Custom Stack (target architecture)

When the Mem0 adapter's limitations become blocking, build a custom backend:

| Component | Technology | License | Role |
|-----------|-----------|---------|------|
| **Graph DB** | Neo4j CE (Docker) | GPLv3 (server) / Apache 2.0 (driver) | Semantic + social memory, org knowledge graph |
| **Vector DB** | Qdrant (Docker) | Apache 2.0 | Episodic + procedural memory, similarity retrieval |
| **Embeddings (local)** | FastEmbed | Apache 2.0 | ONNX-based, Python 3.14 ready |
| **Embeddings (cloud)** | LiteLLM (existing dep) | MIT | Route to any cloud provider |
| **Metadata** | SQLite → PostgreSQL | Public domain | Structured metadata, operational data |

This moves storage to external containers with full 5-type coverage, bi-temporal
tracking, and graph-native social/semantic memory. Same `MemoryBackend` protocol —
swap via config.

### Why Not Graphiti (as initial)?

1. **Pre-1.0 stability**: v0.28, fast-moving API, Docker image freshness issues.
2. **LLM cost**: Episode ingestion is extremely LLM-heavy (1,000+ API calls per 10k
   chars). Conflicts with our cost-aware design.
3. **Partial coverage**: Only 2-3 of 5 memory types.
4. **Heavier setup**: Requires external graph DB container even for basic usage.

---

## Architecture

### Initial: Mem0 In-Process

Everything runs inside the synthorg-backend Docker container. Persistent data written to
configurable paths on mounted Docker volumes.

```text
┌─────────────────────────────────────────────────────────────────┐
│         synthorg-backend Docker container                     │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                Memory Protocol Layer                       │  │
│  │                                                            │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────┐    │  │
│  │  │ MemoryBackend   │  │ MemoryCapabilities           │    │  │
│  │  │ (protocol)      │  │ (capability discovery)       │    │  │
│  │  └────────┬────────┘  └─────────────────────────────┘    │  │
│  │           │                                               │  │
│  │  ┌────────┴───────────────────────────────────────────┐   │  │
│  │  │           Mem0MemoryBackend (adapter)               │   │  │
│  │  │                                                     │   │  │
│  │  │  ┌─────────────┐  ┌────────────┐  ┌────────────┐  │   │  │
│  │  │  │ Mem0 Memory │  │  Qdrant    │  │  SQLite    │  │   │  │
│  │  │  │ (engine)    │  │ (embedded) │  │ (history)  │  │   │  │
│  │  │  │             │  │            │  │            │  │   │  │
│  │  │  │ extraction, │  │ vectors →  │  │ history →  │  │   │  │
│  │  │  │ compression │  │ /data/mem/ │  │ /data/mem/ │  │   │  │
│  │  │  └─────────────┘  └────────────┘  └────────────┘  │   │  │
│  │  └────────────────────────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Mounted volume: /data/memory/  (configurable)                  │
└─────────────────────────────────────────────────────────────────┘
```

### Future: Custom Stack with External Services

When Mem0's limitations become blocking, swap to custom backend via config:

```text
┌──────────────────────────────────────────────────────────────────┐
│         synthorg-backend Docker container                      │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                Memory Protocol Layer                        │  │
│  │                                                             │  │
│  │  ┌─────────────────┐                                       │  │
│  │  │ MemoryBackend   │  (same protocol, different impl)      │  │
│  │  └────────┬────────┘                                       │  │
│  │           │                                                 │  │
│  │  ┌────────┴──────────────────────────────────────────┐     │  │
│  │  │        CustomMemoryBackend                         │     │  │
│  │  │  working  → in-process                             │     │  │
│  │  │  episodic → Qdrant (external)                      │     │  │
│  │  │  semantic → Neo4j (external) + Qdrant              │     │  │
│  │  │  procedural → Qdrant (external)                    │     │  │
│  │  │  social   → Neo4j (external)                       │     │  │
│  │  └───────┬──────────────────────┬─────────────────────┘     │  │
│  └──────────┼──────────────────────┼──────────────────────────┘  │
│             │ bolt://              │ http/grpc                    │
└─────────────┼──────────────────────┼─────────────────────────────┘
              │                      │
┌─────────────┴────────┐  ┌─────────┴────────┐
│  Neo4j CE (Docker)    │  │  Qdrant (Docker)  │
│  Port 7687            │  │  Port 6333/6334   │
└───────────────────────┘  └──────────────────┘
```

### Configuration

Memory config lives in the **same config schema** as all other settings
(`RootConfig` in `config/schema.py`), following the same Pydantic validation and
YAML loading patterns. Per-agent overrides via `AgentConfig.memory` (already exists
as a raw dict field). When the dynamic config system is built, memory config
participates like every other config section.

> **Note:** The `RootConfig.memory` field exists with `CompanyMemoryConfig`
> defaults (see `config/schema.py`).  The Mem0 adapter (#41) will connect the
> config values to an actual backend instance during startup.

```yaml
# Company-wide defaults (in RootConfig)
memory:
  backend: "mem0"              # mem0, custom, cognee, graphiti (future)
  level: "persistent"           # none, session, project, persistent
  storage:
    data_dir: "/data/memory"   # mounted Docker volume path
    vector_store: "qdrant"     # qdrant (embedded), qdrant-external, etc.
    history_store: "sqlite"    # sqlite, postgresql
  embeddings:
    provider: "fastembed"      # fastembed (local), openai, litellm, ollama
    model: "BAAI/bge-small-en-v1.5"
  graph:
    enabled: false             # enable graph memory (requires graph_store)
    store: "neo4j"             # neo4j, falkordb
    uri: "bolt://neo4j:7687"
  options:
    retention_days: null        # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"

# Per-agent overrides (in AgentConfig — list of agent objects)
agents:
  - name: "senior_dev"
    memory:
      level: "persistent"
      graph:
        enabled: true          # this agent gets graph memory
  - name: "intern"
    memory:
      level: "session"         # this agent only keeps session memory
```

### Per-Agent Isolation

Mem0 provides four-level scoping out of the box:
- `user_id` — maps to agent identity
- `agent_id` — per-agent namespace within a user
- `app_id` — multi-tenant isolation (maps to company)
- `run_id` — ephemeral session/task scope

---

## Consequences

### Impact on #32 (Memory Interface Design)

The protocol will follow our established `@runtime_checkable` pattern:

```python
@runtime_checkable
class MemoryBackend(Protocol):
    """Structural interface for agent memory storage backends."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def backend_name(self) -> NotBlankStr: ...

    async def store(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr: ...
    async def retrieve(self, agent_id: NotBlankStr, query: MemoryQuery) -> tuple[MemoryEntry, ...]: ...
    async def get(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> MemoryEntry | None: ...
    async def delete(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool: ...
    async def count(self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None) -> int: ...

@runtime_checkable
class MemoryCapabilities(Protocol):
    """Capability discovery — what this backend supports."""

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]: ...
    @property
    def supports_graph(self) -> bool: ...
    @property
    def supports_temporal(self) -> bool: ...
    @property
    def supports_vector_search(self) -> bool: ...
    @property
    def supports_shared_access(self) -> bool: ...
    @property
    def max_memories_per_agent(self) -> int | None: ...
```

Initial concrete implementation: `Mem0MemoryBackend` (wraps Mem0 `AsyncMemory`).
Future: `CustomMemoryBackend` (Neo4j + Qdrant), or `CogneeMemoryBackend`,
`GraphitiMemoryBackend` — all behind the same protocol.

### Impact on #36 (Persistence)

The memory layer handles **agent memory persistence** only. Operational data (tasks,
costs, messages, audit logs) remains in **SQLite** (upgrading to PostgreSQL later),
managed by separate repositories. This clean separation means:

- `memory/` module: agent memories via Mem0 (initial) or custom backend (future)
- `budget/tracker.py`: cost records via SQLite/Postgres repository
- `engine/`: task state via SQLite/Postgres repository
- `communication/`: message history via SQLite/Postgres repository

Memory data persists to a configurable directory on a mounted Docker volume.

### Impact on #125 (Org Memory Backends)

The `OrgMemoryBackend` protocol (§7.4) mapping:

| Backend | Initial (Mem0) | Future (custom) |
|---------|---------------|-----------------|
| Backend 1: Hybrid Prompt + Retrieval (MVP) | Mem0 vector search for extended knowledge + SQLite for core policies | Same, or Qdrant external |
| Backend 2: GraphRAG Knowledge Graph | Mem0 with graph enabled (Neo4j) | Neo4j with custom entity extraction |
| Backend 3: Temporal Knowledge Graph | Not supported by Mem0 (basic timestamps only) | Neo4j with temporal properties, or Graphiti if stable |

### Embedding Provider Strategy

Mem0 natively supports 11+ embedding providers — configurable via memory config:

- **Local** (default, cost-free): FastEmbed, HuggingFace, Ollama, LM Studio
- **Cloud** (higher quality): OpenAI, Azure OpenAI, Vertex AI, Gemini, Together,
  AWS Bedrock
- **Abstraction**: LangChain embeddings

Configuration determines which provider to use. Set via YAML config.

### Graph DB Strategy

- **Initially**: Graph disabled by default. Enable via config when needed.
- **When enabled**: Mem0 supports Neo4j (recommended), Memgraph, FalkorDB.
- **Kuzu NOT recommended**: Archived October 10, 2025. Its architectural
  concurrency model (single `Database` per process with `Connection` reuse) is not
  suited for Mem0's multi-threaded context. Use Neo4j or FalkorDB instead, both of
  which handle concurrent access patterns out of the box.
- **Future custom backend**: Neo4j as primary graph DB, behind a `GraphDriver`
  protocol for pluggability.

### Incremental Build Path

| Phase | What | External Containers | Notes |
|-------|------|-------------------|-------|
| **Phase 1** | Mem0 in-process (Qdrant embedded + SQLite) | None | All memory inside synthorg-backend container. Persists to mounted volume |
| **Phase 2** | Enable Mem0 graph (Neo4j) | 1 (Neo4j) | Optional, for semantic/social memory and org knowledge graph |
| **Phase 3** | Custom backend OR swap to Cognee/Graphiti | 2 (Neo4j + Qdrant) | When Mem0 limitations become blocking, or when alternatives add 3.14 support |

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Mem0 flat fact model limits 5-type taxonomy | Certain | Low (initial) | Acceptable for initial backend. Metadata tagging provides partial typing. Custom backend replaces when needed |
| Mem0 API breaking changes | Possible | Medium | Pin `mem0ai` version. Adapter layer isolates changes from protocol consumers |
| Mem0 Python 3.14 untested (range allows it) | Possible | High | `>=3.9,<4.0` allows 3.14 but no explicit classifier. Test early in CI. Fallback: custom backend |
| Neo4j CE resource footprint (JVM, ~512 MB+ RAM) | Likely | Low | Deferred to Phase 2. Not needed initially. FalkorDB as lighter alternative |
| Kuzu ecosystem fragmentation | Likely | None | Archived Oct 2025. Not recommended. All candidates support Neo4j/FalkorDB. Not a factor |

---

## Alternatives Considered

### Custom Stack as Initial Backend

Highest score (80/100) on architectural fit, but ~6-8k lines of custom code before
any memory works. Deferred to future phase — build after Mem0 proves the protocol
shape and reveals real-world requirements.

### Graphiti for Temporal KG + Custom for Rest

Appealing for §7.4 Backend 3, but:
- Pre-1.0 stability risk for a core subsystem
- Extreme LLM ingestion costs conflict with cost-aware design
- Would still need Qdrant for episodic/procedural memory
- Temporal tracking can be implemented with Neo4j temporal properties

### Cognee (Best Backend Flexibility)

Most flexible backend support (4+ graph DBs, 3+ vector stores), but:
- **Python `<3.14` not yet supported** — conservative upper bound, no known technical
  blocker. Check future releases.
- Early-stage maturity (v0.5.x)
- Could become an alternative backend behind our protocol once 3.14 lands.

### Letta (OS-Inspired Memory)

Architecturally unique self-editing memory paradigm, but:
- **Python `<3.14` not yet supported** — conservative upper bound. Check future releases.
- Full agent platform, not a standalone memory layer
- Cannot use memory component independently
- Opinionated architecture conflicts with our pluggable protocol design.

---

## Backend Swappability (Key Design Principle)

The protocol-based architecture means **the memory layer decision is never final**.
Any backend that satisfies the `MemoryBackend` protocol can be added as an alternative
implementation. **Mem0 is the initial backend**, not the only one.

Future backends can be added without modifying existing code:

| Candidate | Trigger to Revisit | Role in the Architecture |
|-----------|-------------------|--------------------------|
| **Custom Stack** | Mem0 adapter limitations become blocking | Full 5-type coverage, bi-temporal tracking, graph-native memory |
| **Cognee** | Adds Python 3.14 support | Could provide a unified graph+vector pipeline behind the memory protocol |
| **Letta** | Adds Python 3.14 support + standalone memory extraction | Could power self-editing memory for advanced agents |
| **Graphiti** | Reaches v1.0 + reduces LLM ingestion costs | Could power §7.4 Backend 3 (temporal KG) specifically |

Capability discovery flags (`supports_graph`, `supports_temporal`, etc.) enable
backends with different feature sets to coexist. An agent configured for graph memory
will use a backend that supports it; one that doesn't need graph memory can use a
simpler backend.

### Watch List (check periodically)

- [ ] **Cognee** `requires-python` — currently `<3.14`. Monitor releases for bump.
- [ ] **Letta** `requires-python` — currently `<3.14`. Monitor releases for bump.
- [ ] **Mem0** typed memory support — currently flat facts. Monitor for richer taxonomy.
- [ ] **Graphiti** v1.0 — currently v0.28. Monitor for API stabilization + cost reduction.

---

## Component Version References

| Component | Version | PyPI / Docker | Python 3.14 |
|-----------|---------|---------------|-------------|
| Neo4j CE | 5.x | `neo4j:community` (Docker) | N/A (JVM) |
| neo4j (driver) | 6.1.0 | `neo4j` (PyPI) | Confirmed (classifier) |
| Qdrant | 1.13.x | `qdrant/qdrant` (Docker) | N/A (Rust) |
| qdrant-client | 1.17.0 | `qdrant-client` (PyPI) | Confirmed (classifier) |
| FastEmbed | 0.7.4 | `fastembed` (PyPI) | Confirmed (classifier) |
| mem0ai | 1.0.5 | `mem0ai` (PyPI) | PASS (`>=3.9,<4.0`) |
| LiteLLM | (existing dep) | `litellm` (PyPI) | In use |
| SQLite | (stdlib) | Built-in | Yes |

---

## Appendix: Eliminated Candidates Detail

### Letta — NOT YET COMPATIBLE (G7: Python `<3.14`)

- `requires-python = "<3.14,>=3.11"` — likely conservative bound, not technical
- Full agent platform, not standalone memory library
- OS-inspired memory hierarchy (core/archival/recall) is powerful but inflexible
- No graph memory capabilities
- Memory component cannot be extracted from the platform
- **Watch**: revisit when/if 3.14 support is added

### Cognee — NOT YET COMPATIBLE (G7: Python `<3.14`)

- `requires-python = ">=3.10,<3.14"` — likely conservative bound, not technical
- Best multi-backend flexibility (Kuzu, Neo4j, FalkorDB, LanceDB, Qdrant, etc.)
- `memify()` self-improving memory is unique
- 14 search modes including graph completion and temporal
- Would be a strong contender if Python 3.14 constraint is lifted
- **Watch**: revisit when/if 3.14 support is added — strongest alternative backend candidate.

### memU — ELIMINATED (G2: AGPL-3.0)

- Copyleft license incompatible with BUSL-1.1
- Interesting hierarchical file-system memory design
- 92% LOCOMO accuracy, ~1/10 token cost

### Supermemory — ELIMINATED (G1: Hosted API only)

- Python SDK is just an API client for their cloud service
- Not a self-hosted memory framework
- #1 on several benchmarks but requires cloud dependency

### Graphlit — ELIMINATED (G1: Cloud-native only)

- No self-hosting option at all
- SDKs are MIT but the service is cloud-only

### MemOS — DID NOT ADVANCE (Immature)

- Passed gates but ~5.9k stars, early v2.0
- Heavy dependency footprint (Transformers, scikit-learn)
- Unclear multi-tenancy support
- Not enough production usage data to recommend for core subsystem

### Other Tier 2-3 candidates

OpenMemory, Memary, A-MEM, SimpleMem, LangMem, memsearch — all interesting but
either too small/immature, research-oriented, or missing critical features (Docker
support, multi-tenancy, graph capabilities) for our requirements.
