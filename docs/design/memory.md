---
title: Memory & Persistence
description: Agent memory architecture, shared organizational memory, backend protocols, operational data persistence, and memory injection strategies.
---

# Memory & Persistence

The SynthOrg framework separates two distinct storage concerns:

- **Agent memory**: what agents know, remember, and learn (working, episodic, semantic, procedural, social)
- **Operational data**: tasks, cost records, messages, and audit logs generated during execution

Both are implemented behind pluggable protocol interfaces, making storage backends swappable via
configuration without modifying application code.

---

## Memory Architecture

| Working Memory | Episodic Memory | Semantic Memory | Procedural Memory |
|---|---|---|---|
| Current task context | Past events & decisions | Knowledge & facts learned | Skills & how-to |

**Storage Backend:** Mem0 (durable, Qdrant+SQLite), InMemory (session-scoped),
Composite (namespace-based routing adapter). See [Decision Log](../architecture/decisions.md).

Each agent maintains its own memory store. The storage backend is selected via configuration
and all access flows through the [`MemoryBackend`](#memorybackend-protocol) protocol.

---

## Memory Types

| Type | Scope | Persistence | Example |
|------|-------|-------------|---------|
| **Working** | Current task | None (in-context) | "I'm implementing the auth endpoint" |
| **Episodic** | Past events | Configurable | "Last sprint the team chose JWT over sessions" |
| **Semantic** | Knowledge | Long-term | "This project uses Litestar with aiosqlite" |
| **Procedural** | Skills/patterns | Long-term | "Code reviews require 2 approvals here" |
| **Social** | Relationships | Long-term | "The QA lead prefers detailed test plans" |

---

## Memory Levels

Memory persistence is configurable per agent, from no persistence to fully persistent storage.

???+ note "Memory Level Configuration"

    ```yaml
    memory:
      level: "persistent"            # none | session | project | persistent (default: session)
      backend: "mem0"               # mem0 (default); also supports composite, inmemory
      storage:
        data_dir: "/data/memory"    # mounted Docker volume path
        vector_store: "qdrant"      # hardcoded to embedded qdrant in Mem0 backend
        history_store: "sqlite"     # hardcoded to sqlite in Mem0 backend
      options:
        retention_days: null         # null = forever
        max_memories_per_agent: 10000
        consolidation_interval: "daily"  # compress old memories
        shared_knowledge_base: true      # agents can access shared facts
    ```

---

## Shared Organizational Memory

Beyond individual agent memory, the framework provides **organizational memory**: company-wide
knowledge that all agents can access: policies, conventions, architecture decision records (ADRs),
coding standards, and operational procedures. This is not personal episodic memory ("what I did
last Tuesday") but institutional knowledge ("the team always uses Litestar, not Flask").

Shared organizational memory is implemented behind an `OrgMemoryBackend` protocol, making the
system highly modular and extensible. New backends can be added without modifying existing ones.

### Backend 1: Hybrid Prompt + Retrieval (Default)

Critical rules (5--10 items, e.g., "no commits to main," "all PRs need 2 approvals") are injected
into every agent's system prompt. Extended knowledge (ADRs, detailed procedures, style guides) is
stored in a queryable store and retrieved on demand at task start.

```yaml
org_memory:
  backend: "hybrid_prompt_retrieval"    # hybrid_prompt_retrieval, graph_rag, temporal_kg
  core_policies:                        # always in system prompt
    - "All code must have 80%+ test coverage"
    - "Use Litestar, not Flask"
    - "PRs require 2 approvals"
  extended_store:
    backend: "sqlite"                   # sqlite, postgresql
    max_retrieved_per_query: 5
  write_access:
    policies: ["human"]                 # only humans write core policies
    adrs: ["human", "senior", "lead", "c_suite"]
    procedures: ["human", "senior", "lead", "c_suite"]
```

**Strengths:** Simple to implement. Core rules are always present. Extended knowledge scales
with the organization.

**Limitations:** Basic retrieval may miss relational connections between policies.

### Research Directions

The following backends illustrate why `OrgMemoryBackend` is a protocol; the architecture
supports future upgrades without modifying existing code. These are research directions that
may inform future work if organizational memory needs outgrow the Hybrid Prompt + Retrieval
approach.

!!! info "Research Direction: GraphRAG Knowledge Graph"

    Organizational knowledge stored as entities + relationships in a knowledge graph. Agents
    query via graph traversal, enabling multi-hop reasoning: "Litestar is the standard" is
    linked to "don't use Flask," which is linked to "exception: data team uses Django for admin."

    ```yaml
    org_memory:
      backend: "graph_rag"
      graph:
        store: "sqlite"                     # graph stored in relational DB, or dedicated graph DB
        entity_extraction: "auto"           # auto-extract entities from ADRs and policies
    ```

    **Strengths:** Significant accuracy improvement over vector-only retrieval (some benchmarks
    report 3--4x gains). Multi-hop reasoning captures policy relationships.

    **Limitations:** More complex infrastructure. Entity extraction can be noisy. Heavier setup.

!!! info "Research Direction: Temporal Knowledge Graph"

    Like GraphRAG but tracks how facts change over time. "The team used Flask until March 2026,
    then switched to Litestar." Agents see current truth but can query history for context.

    ```yaml
    org_memory:
      backend: "temporal_kg"
      temporal:
        track_changes: true
        history_retention_days: null        # null = forever
    ```

    **Strengths:** Handles policy evolution naturally. Agents understand when and why things changed.

    **Limitations:** Most complex. Potentially overkill for small organizations or local-first use.

!!! tip "Migration Path to GraphRAG (Phase 2)"

    If multi-hop organizational reasoning becomes a requirement (e.g., tracing which policies
    affect hiring across salary, budget, and compliance domains), the upgrade path is
    non-breaking:

    1. Add post-consolidation entity extraction as a new `EntityExtractionStrategy` in the
       consolidation pipeline (entities stored in a separate `EntityStore` protocol).
    2. Create `GraphRAGMemoryBackend` implementing the existing `MemoryBackend` protocol with
       graph-traversal queries alongside standard vector retrieval.
    3. Enable via config; existing application code is unchanged.

    See [Decision Log](../architecture/decisions.md) D25 for the full trade-off analysis and
    deferral rationale.

### OrgMemoryBackend Protocol

All backends implement the `OrgMemoryBackend` protocol:

- `query(OrgMemoryQuery) -> tuple[OrgFact, ...]`
- `write(OrgFactWriteRequest, *, author: OrgFactAuthor) -> NotBlankStr`
- `list_policies() -> tuple[OrgFact, ...]`
- Lifecycle methods: `connect`, `disconnect`, `health_check`, `is_connected`, `backend_name`

The MVP ships with Backend 1 (Hybrid Prompt + Retrieval). The selected memory layer backend
Mem0 ([Decision Log](../architecture/decisions.md)) provides optional graph memory via Neo4j/FalkorDB, which could reduce
implementation effort for the research direction backends.

!!! tip "Write Access Control"

    Core policies are human-only. ADRs and procedures can be written by senior+ agents. All
    writes are append-only and auditable. This prevents agents from corrupting shared organizational
    knowledge while allowing senior agents to document decisions.

---

## Memory Backend Protocol

Agent memory is implemented behind a pluggable `MemoryBackend` protocol with three concrete
implementations: Mem0 (durable, Qdrant+SQLite), InMemory (session-scoped), and Composite
(namespace-based routing adapter); see [Decision Log](../architecture/decisions.md). Application
code depends only on the protocol; the storage engine is an implementation detail swappable via
config.

### Enums

| Enum | Values | Purpose |
|------|--------|---------|
| `MemoryCategory` | WORKING, EPISODIC, SEMANTIC, PROCEDURAL, SOCIAL | Memory type categories |
| `MemoryLevel` | PERSISTENT, PROJECT, SESSION, NONE | Persistence level per agent |
| `ConsolidationInterval` | HOURLY, DAILY, WEEKLY, NEVER | How often old memories are compressed |

### MemoryBackend Protocol

```python
@runtime_checkable
class MemoryBackend(Protocol):
    """Lifecycle + CRUD for agent memory storage."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def backend_name(self) -> NotBlankStr: ...

    async def store(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr:
        """Raises: MemoryConnectionError, MemoryStoreError."""
        ...
    async def retrieve(self, agent_id: NotBlankStr, query: MemoryQuery) -> tuple[MemoryEntry, ...]:
        """Raises: MemoryConnectionError, MemoryRetrievalError."""
        ...
    async def get(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> MemoryEntry | None:
        """Raises: MemoryConnectionError, MemoryRetrievalError."""
        ...
    async def delete(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool:
        """Raises: MemoryConnectionError, MemoryStoreError."""
        ...
    async def count(self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None) -> int:
        """Raises: MemoryConnectionError, MemoryRetrievalError."""
        ...
```

### MemoryCapabilities Protocol

Backends that implement `MemoryCapabilities` expose what features they support, enabling
runtime capability checks before attempting operations.

```python
@runtime_checkable
class MemoryCapabilities(Protocol):
    """Capability discovery for memory backends."""

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

### SharedKnowledgeStore Protocol

Backends that support cross-agent shared knowledge implement this protocol alongside
`MemoryBackend`. Not all backends require cross-agent queries; this keeps the base protocol
clean.

```python
@runtime_checkable
class SharedKnowledgeStore(Protocol):
    """Cross-agent shared knowledge operations."""

    async def publish(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr:
        """Raises: MemoryConnectionError, MemoryStoreError."""
        ...
    async def search_shared(self, query: MemoryQuery, *, exclude_agent: NotBlankStr | None = None) -> tuple[MemoryEntry, ...]:
        """Raises: MemoryConnectionError, MemoryRetrievalError."""
        ...
    async def retract(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool:
        """Raises: MemoryConnectionError, MemoryStoreError."""
        ...
```

See [Multi-Agent Memory Consistency](memory-consistency.md) for the consistency model used
when multiple agents share a `SharedKnowledgeStore`, including MVCC snapshot reads,
append-only write semantics, and conflict handling.

### Error Hierarchy

All memory errors inherit from `MemoryError` so callers can catch the entire family with a
single except clause.

| Error | When Raised |
|-------|------------|
| `MemoryError` | Base exception for all memory operations |
| `MemoryConnectionError` | Backend connection cannot be established or is lost |
| `MemoryStoreError` | A store or delete operation fails |
| `MemoryRetrievalError` | A retrieve, search, or count operation fails |
| `MemoryNotFoundError` | A specific memory ID is not found |
| `MemoryConfigError` | Memory configuration is invalid |
| `MemoryCapabilityError` | An unsupported operation is attempted for a backend |
| `FineTuneDependencyError` | ML dependencies (torch, sentence-transformers) are missing |
| `FineTuneCancelledError` | A fine-tuning pipeline run is cancelled |

### Configuration

```yaml
memory:
  backend: "mem0"
  level: "persistent"              # none, session, project, persistent (default: session)
  storage:
    data_dir: "/data/memory"
    vector_store: "qdrant"          # hardcoded to embedded qdrant in Mem0 backend
    history_store: "sqlite"         # hardcoded to sqlite in Mem0 backend
  options:
    retention_days: null            # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"
    shared_knowledge_base: true

# Embedder config is passed programmatically via the factory:
#   create_memory_backend(config, embedder=Mem0EmbedderConfig(
#       provider="<embedding-provider>",
#       model="<embedding-model-id>",
#       dims=1536,
#   ))
```

Configuration is modeled by `CompanyMemoryConfig` (top-level), `MemoryStorageConfig`
(storage paths/backends), and `MemoryOptionsConfig` (behaviour tuning). All are frozen
Pydantic models. The `create_memory_backend(config, *, embedder=...)` factory returns an
isolated `MemoryBackend` instance per company. The `embedder` kwarg is required for the
Mem0 backend (must be a `Mem0EmbedderConfig`).

### Embedding Model Selection

Embedding model quality directly determines memory retrieval accuracy. The
[LMEB benchmark](https://arxiv.org/abs/2603.12572) (Zhao et al., March 2026) evaluates embedding
models on long-horizon memory retrieval across four types that map directly to SynthOrg's
`MemoryCategory` enum:

| SynthOrg Category | LMEB Category | Evaluation Priority |
|-------------------|---------------|---------------------|
| EPISODIC | Episodic (69 tasks) | High |
| PROCEDURAL | Procedural (67 tasks) | High |
| SEMANTIC | Semantic (15 tasks) | Medium |
| SOCIAL | Dialogue (42 tasks) | Medium |
| WORKING | N/A (in-context) | N/A |

**MTEB scores do not predict memory retrieval quality** (Pearson: -0.115, Spearman: -0.130).
Embedding model selection must be evaluated on LMEB, not MTEB. See
[Decision Log](../architecture/decisions.md) and the
[Embedding Evaluation](../reference/embedding-evaluation.md) reference page for the full analysis,
model rankings, and deployment tier recommendations.

Key findings:

- Larger models do not always outperform smaller ones on memory retrieval
- Dialogue/social memory is the hardest retrieval category for all models
- Instruction sensitivity varies per model; must be validated per deployment
- Three deployment tiers are recommended: full-resource (7-12B), mid-resource (1-4B), and
  CPU-only (< 1B)

**Tier inference inputs** (`auto_select_embedder`):

- **`provider_preset_name`**: first registered provider name, read from the provider registry at setup-completion time.  When operators use preset names verbatim as provider names (the wizard default), the preset hint steers tier selection; otherwise tier inference falls back to heuristic defaults.
- **`api.setup.has_gpu`** (yaml path; setting key `setup_has_gpu` under namespace `API`): operator-owned boolean, default `"false"`, advanced level.  Flipped by the setup wizard (or directly by an operator) and read via `_read_has_gpu_setting(settings_service)`.  Accepts `true`/`1`/`yes` (→ True) and `false`/`0`/`no`/empty (→ False), case-insensitive; any other value returns `None` (unknown) silently, while a settings-service read failure logs a WARNING and also returns `None`.  There is no platform probe today; the signal is operator-declared, not auto-detected.

Tier fallback is not a single CPU/GPU switch.  `auto_select_embedder` uses preset-name and capability heuristics to pick a `GPU_CONSUMER` or `GPU_FULL` tier when `has_gpu` is `True` or `None`; the tier only collapses to the CPU-only default when the operator has selected a local/self-hosted preset *and* has explicitly set `has_gpu=False`.  Missing or unparseable inputs degrade gracefully and never block setup completion.

### Domain-Specific Embedding Fine-Tuning

Domain-specific fine-tuning can improve retrieval quality by 10-27% over base models
([NVIDIA evaluation](https://huggingface.co/blog/nvidia/domain-specific-embedding-finetune)).
The pipeline requires no manual annotation and runs on a single GPU.

**Pipeline stages:**

1. **Synthetic data generation**: LLM generates query-document pairs from org documents
   (policies, ADRs, procedures, coding standards)
2. **Hard negative mining**: base model embeds all passages; top-k semantically similar
   but non-matching passages become hard negatives
3. **Contrastive fine-tuning**: biencoder training with InfoNCE loss (tau=0.02, 3 epochs,
   lr=1e-5). Single GPU, 1-2 hours for ~500 documents
4. **Evaluation**: NDCG@10 and Recall@10 comparison of the fine-tuned checkpoint against
   the base model on held-out validation data
5. **Deploy**: save checkpoint; update `Mem0EmbedderConfig` to point to fine-tuned model

**Integration design:** fine-tuning is an offline pipeline triggered via
`POST /admin/memory/fine-tune` (see `MemoryAdminController`). The optional
`EmbeddingFineTuneConfig` (disabled by default) stores the checkpoint path. When
`enabled=True` and `checkpoint_path` is set, backend initialization uses the
checkpoint path as the model identifier passed to the Mem0 SDK. The embedding
provider must serve the fine-tuned model under this identifier.

**Container execution:** when `FineTuneExecutionConfig.backend` is `"docker"`, each
pipeline stage runs inside an ephemeral `synthorg-fine-tune-gpu` (default) or
`synthorg-fine-tune-cpu` container spawned by the backend via the Docker API. Both
variants ship the same Python runner and accept the same stage-config contract; they
differ only in the bundled torch build (CUDA ~4 GB vs CPU ~1.7 GB) and whether GPU
passthrough is usable. The variant is selected at `synthorg init` time (fresh installs)
or via `synthorg config set fine_tuning_variant gpu|cpu` (post-init, preserves data)
and persisted as `fine_tuning_variant` in `config.json`. The backend consumes
`SYNTHORG_FINE_TUNE_IMAGE` verbatim as a full image reference (including registry,
repository, and either a `:tag` or a digest-pinned `@sha256:...`); in a CLI-managed
install the rendered `compose.yml` writes the verified digest-pinned ref into this
env var automatically. Operators running a hand-managed `compose.yml` without the
CLI set `SYNTHORG_FINE_TUNE_IMAGE` on the backend directly; tag-based refs work
for quick evaluation, but production deployments should pin a digest so the backend
spawns the exact attested image. See [Deployment &rarr; Fine-Tuning (optional)](../guides/deployment.md#fine-tuning-optional)
for the BYO snippet. The container reads stage configuration
from `/etc/fine-tune/config.json`, executes the pipeline function, and emits
structured progress markers (`STAGE_START:`, `STAGE_COMPLETE:`) on stdout. The
orchestrator will parse these markers from container logs for progress reporting
(orchestrator integration is planned; the runner and markers are implemented).
Source data is mounted at `/data` (read-only), checkpoints written to `/checkpoints`
(read-write). GPU passthrough is available via `gpu_enabled=True` (only meaningful
for the GPU variant). The in-process fallback (`backend="in-process"`) is preserved
for non-Docker deployments where torch is installed directly.

```python
class EmbeddingFineTuneConfig(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    enabled: bool = False
    checkpoint_path: NotBlankStr | None = None
    base_model: NotBlankStr | None = None
    training_data_dir: NotBlankStr | None = None
```

When `enabled=True`, both `checkpoint_path` and `base_model` are required
(enforced by model validation).  Path traversal (`..`) and Windows-style
paths are rejected to prevent container path escapes.

The `FineTuningPipeline` protocol formalizes the five stages:

```python
class FineTuningPipeline(Protocol):
    async def generate_training_data(self, source_dir: str) -> Path: ...
    async def mine_hard_negatives(self, training_data: Path) -> Path: ...
    async def fine_tune(self, training_data: Path, base_model: str) -> Path: ...
    async def evaluate(self, checkpoint: Path, base_model: str, validation_data: Path) -> EvalMetrics: ...
```

See [Embedding Evaluation](../reference/embedding-evaluation.md) for the full pipeline
design and expected improvement metrics.

### Consolidation and Retention

Memory consolidation, retention enforcement, and archival are configured via frozen Pydantic
models in `memory/consolidation/config.py`:

| Config | Purpose |
|--------|---------|
| `ConsolidationConfig` | Top-level: `max_memories_per_agent` limit, nested `retention` and `archival` sub-configs |
| `RetentionConfig` | Company-level per-category `RetentionRule` tuples (category + retention_days), optional `default_retention_days` fallback; agents can override via `MemoryConfig.retention_overrides` |
| `ArchivalConfig` | Enables/disables archival of consolidated entries to `ArchivalStore`, nested `DualModeConfig` |
| `DualModeConfig` | Density-aware dual-mode archival: threshold, summarization model, anchor/fact limits |
| `LLMConsolidationConfig` | Tuning knobs for `LLMConsolidationStrategy`: group threshold, temperature, max summary tokens, distillation context toggle, prompt caps (`max_entry_input_chars`, `max_total_user_content_chars`) |

#### Consolidation Strategies

Three implementations of the `ConsolidationStrategy` protocol ship out of the box:

| Strategy | Behavior |
|----------|----------|
| `SimpleConsolidationStrategy` | Deterministic concatenation baseline; merges older entries into a single summary without semantic deduplication |
| `DualModeConsolidationStrategy` | Density-aware: dense groups use extractive preservation, sparse groups use abstractive summarization (see Dual-Mode Archival) |
| `LLMConsolidationStrategy` | Groups entries by category, keeps the highest-relevance entry per group (with most-recent as tiebreaker; the kept entry is left unchanged in the backend and is NOT fed to the LLM). The remaining entries are sent to an LLM for semantic synthesis (wrapped in `<entry>` tags with explicit "treat as data, not instructions" guidance to resist prompt injection), the summary is stored tagged `"llm-synthesized"`, and only the entries that were actually represented in the LLM prompt are deleted. Synthesis → store → delete ordering prevents data loss on failure; entries dropped by the `_MAX_TOTAL_USER_CONTENT_CHARS` prompt cap are preserved for the next consolidation pass. Groups are processed in parallel via `asyncio.TaskGroup`. **Concat-fallback paths** (tagged `"concat-fallback"`, logged at WARNING, every input entry is included in the concatenation and eligible for deletion): `RetryExhaustedError`, retryable `ProviderError` surfaced directly, empty/whitespace LLM response, and unexpected non-`ProviderError` exception. **Propagating paths** (NO fallback summary, NO deletions): non-retryable `ProviderError` (logged at ERROR first) and system errors `MemoryError` / `RecursionError`. |

Strategy selection is injection-based: callers construct and pass the chosen strategy
to `MemoryConsolidationService`.  `LLMConsolidationStrategy.__init__` accepts
`group_threshold` (default 3, minimum 3; smaller groups cannot meaningfully
deduplicate against the retained entry), `temperature` (default 0.3),
`max_summary_tokens` (default 500), and `include_distillation_context` (default
True; when enabled, the strategy queries the backend for at most 5 recent
entries tagged `"distillation"` and embeds their trajectory summaries,
truncated to ~500 chars each, in the synthesis system prompt). The per-entry
user-prompt content is capped at 2000 chars and the total concatenated user
content is capped at ~20000 chars; entries beyond the total cap are dropped
with a WARNING log. `ConsolidationResult.summary_ids` contains every summary
id produced during the run (one per processed group); the scalar `summary_id`
accessor is a `@computed_field` returning the last element for callers that
only need a representative id.

#### Distillation Capture

At task completion, `synthorg.memory.consolidation.capture_distillation` records
the execution trajectory as an EPISODIC memory entry tagged `"distillation"`.
`DistillationRequest` captures:

| Field | Source |
|-------|--------|
| `agent_id`, `task_id` | Caller context |
| `trajectory_summary` | Turn count, total tokens, unique tools, total tool calls |
| `outcome` | `TerminationReason` + optional error message |
| `memory_tool_invocations` | `MemoryToolName` enum values (`SEARCH_MEMORY`, `RECALL_MEMORY`) extracted from `TurnRecord.tool_calls_made` (NOT memory entry IDs; typed enum members, counted per invocation) |
| `created_at` | Capture timestamp |

`AgentEngine` wires this into `_post_execution_pipeline` when
`distillation_capture_enabled=True` is passed to the constructor (default False
for opt-in behavior).  Capture fires regardless of termination reason;
successful runs, errors, timeouts, and budget exhaustions all produce useful
trajectory context for downstream consolidation.  The helper is non-critical:
non-system failures log at WARNING and return `None`; system errors
(`builtins.MemoryError`, `RecursionError`) propagate.

Downstream, `LLMConsolidationStrategy` picks these entries up by tag query
when synthesizing category groups, embedding the trajectory summaries and
outcomes in the synthesis system prompt so the LLM has context about what the
agent was trying to accomplish when the memories it is merging were created.

#### Dual-Mode Archival

When `ArchivalConfig.dual_mode.enabled` is `True`, consolidation classifies content density before
choosing an archival mode. This prevents catastrophic information loss from naively summarizing
dense content (code, structured data, identifiers). Based on research: Memex
([arXiv:2603.04257](https://arxiv.org/abs/2603.04257)) and KV Cache Attention Matching
([arXiv:2602.16284](https://arxiv.org/abs/2602.16284)).

| Density | Archival Mode | Method |
|---------|--------------|--------|
| Sparse (conversational, narrative) | `ABSTRACTIVE` | LLM-generated summary via `AbstractiveSummarizer` |
| Dense (code, structured data, IDs) | `EXTRACTIVE` | Verbatim key-fact extraction + start/mid/end anchors via `ExtractivePreserver` |

**Classification** is heuristic-based (`DensityClassifier`), using five weighted signals: code
patterns, structured data markers, identifier density, numeric density, and line structure.  No LLM
is needed for classification; only for abstractive summarization.  Groups are classified by
majority vote: if most entries in a category group are dense, the group uses extractive mode.

**Deterministic restore**: When entries are archived, the service builds an `archival_index`
(mapping `original_id` → `archival_id`) on `ConsolidationResult`.  Agents can use this index to
call `ArchivalStore.restore(agent_id, entry_id)` directly by ID, bypassing semantic search.

| Model | Purpose |
|-------|---------|
| `ArchivalMode` | Enum: `ABSTRACTIVE` or `EXTRACTIVE` |
| `ArchivalModeAssignment` | Maps a removed entry ID to its archival mode (set by strategy) |
| `ArchivalIndexEntry` | Maps original entry ID to archival store ID (built by service) |

#### Per-Agent Retention Overrides

Individual agents can override company-level retention rules via
`MemoryConfig.retention_overrides` (per-category) and
`MemoryConfig.retention_days` (agent-level default).

Resolution order per category:

1. Agent per-category rule
2. Company per-category rule
3. Agent global default
4. Company global default
5. Keep forever (no expiry)

---

## Operational Data Persistence

Agent memory is handled by the `MemoryBackend` protocol (Mem0 initial, custom stack future;
see [Decision Log](../architecture/decisions.md)). **Operational data** (tasks, cost records, messages, audit logs) is a separate
concern managed by a pluggable `PersistenceBackend` protocol. Application code depends only on
repository protocols; the storage engine is an implementation detail swappable via config.

### Architecture

```d2
Application Code: {
  Repos: "Repository Protocols" {
    grid-columns: 4
    Task Repo: "Task Repo\n(engine/)"
    Cost Repo: "Cost Repo\n(budget/)"
    Message Repo: "Message Repo\n(communication/)"
    Audit Repo: "Audit Repo\n(security/)"
  }

  Backend: |
    PersistenceBackend (protocol)
    connect() . disconnect() . health_check() . migrate()
  |

  Impls: |
    SQLitePersistenceBackend (implemented)
    PostgresPersistenceBackend (implemented)
    MariaDBPersistenceBackend (planned)
  |

  Repos -> Backend -> Impls
}
```

### Protocol Design

```python
@runtime_checkable
class PersistenceBackend(Protocol):
    """Lifecycle management for operational data storage."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...
    async def migrate(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def backend_name(self) -> NotBlankStr: ...

    @property
    def tasks(self) -> TaskRepository: ...
    @property
    def cost_records(self) -> CostRecordRepository: ...
    @property
    def messages(self) -> MessageRepository: ...
    # ... plus lifecycle_events, task_metrics, collaboration_metrics,
    #     parked_contexts, audit_entries, users, api_keys, checkpoints,
    #     heartbeats, agent_states, settings, artifacts, projects,
    #     custom_presets
```

Each entity type has its own repository protocol:

```python
@runtime_checkable
class TaskRepository(Protocol):
    """CRUD + query interface for Task persistence."""

    async def save(self, task: Task) -> None: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def list_tasks(self, *, status: TaskStatus | None = None, assigned_to: str | None = None, project: str | None = None) -> tuple[Task, ...]: ...
    async def delete(self, task_id: str) -> bool: ...

@runtime_checkable
class CostRecordRepository(Protocol):
    """CRUD + aggregation interface for CostRecord persistence."""

    async def save(self, record: CostRecord) -> None: ...
    async def query(self, *, agent_id: str | None = None, task_id: str | None = None) -> tuple[CostRecord, ...]: ...
    async def aggregate(self, *, agent_id: str | None = None) -> float: ...

@runtime_checkable
class MessageRepository(Protocol):
    """CRUD + query interface for Message persistence."""

    async def save(self, message: Message) -> None: ...
    async def get_history(self, channel: str, *, limit: int | None = None) -> tuple[Message, ...]: ...
```

### Configuration

```yaml
persistence:
  backend: "sqlite"                   # sqlite, postgres (mariadb future)
  sqlite:
    path: "/data/synthorg.db"       # database file path (mounted volume in Docker)
    wal_mode: true                    # WAL for concurrent read performance
    journal_size_limit: 67108864      # 64 MB WAL journal limit
  postgres:                           # requires `synthorg[postgres]` extra
    host: "db.internal"
    port: 5432
    database: "synthorg"
    username: "synthorg_app"
    password: "${POSTGRES_PASSWORD}"   # SecretStr -- redacted from logs
    ssl_mode: "verify-full"            # production: verify-full authenticates the server's certificate chain AND hostname; "require" only encrypts the transport without verifying identity (MITM-vulnerable). Use verify-ca or verify-full in any deployment exposed to a network path you do not fully control.
    pool_min_size: 1
    pool_max_size: 10
    pool_timeout_seconds: 30.0
    application_name: "synthorg"
    statement_timeout_ms: 30000        # server-side per-query limit
    connect_timeout_seconds: 10.0
  # mariadb:                          # future
  #   url: "mariadb://user:pass@host:3306/synthorg"
  #   pool_size: 10
```

### Entities Persisted

| Entity | Source Module | Repository | Key Queries |
|--------|-------------|------------|-------------|
| `Task` | `core/task.py` | `TaskRepository` | by status, by assignee, by project |
| `CostRecord` | `budget/cost_record.py` | `CostRecordRepository` | by agent, by task, aggregations |
| `Message` | `communication/message.py` | `MessageRepository` | by channel |
| `AuditEntry` | `security/models.py` | `AuditRepository` | by agent, by action type, by verdict, by risk level, time range |
| `ParkedContext` | `security/timeout/parked_context.py` | `ParkedContextRepository` | by execution_id, by agent_id, by task_id |
| `AgentRuntimeState` | `engine/agent_state.py` | `AgentStateRepository` | by agent_id, active agents |
| Setting | `settings/models.py` | `SettingsRepository` | by namespace+key, by namespace, all |
| `Artifact` | `core/artifact.py` | `ArtifactRepository` | by task_id, by created_by, by artifact_type |
| `HandoffArtifact` | `engine/workflow/handoff.py` | (in-memory, per-execution frame) | Structured inter-stage handoff; `artifact_refs` resolve through `ArtifactRepository`. See [Verification & Quality: Verification Stage](verification-quality.md#verification-stage) |
| `Project` | `core/project.py` | `ProjectRepository` | by status, by lead |
| `DecisionRecord` | `engine/decisions.py` | `DecisionRepository` | by task_id (version ASC), by agent (role=executor or reviewer, recorded_at DESC) |
| Custom preset | `templates/preset_service.py` | `PersonalityPresetRepository` | by name |

### Schema Strategy

- **Declarative migrations via [Atlas](https://atlasgo.io/)**: `schema.sql` defines the desired state; `atlas migrate diff` generates versioned SQL in `revisions/`
- At startup, `PersistenceBackend.migrate()` invokes `atlas migrate apply` to apply pending revisions
- Atlas tracks applied versions in its `atlas_schema_revisions` table (no hand-rolled version tracking)
- Both persistence and ontology tables are consolidated into a single `schema.sql` (same database file)
- CI runs `atlas migrate validate` (migration integrity) and `atlas schema diff` (drift detection) on every PR
- Squashing: run `atlas migrate squash` during the release process when migration count exceeds 50

### Key Principles

Application code never imports a concrete backend
:   Only repository protocols are used. This ensures complete decoupling from the storage engine.

Adding a new backend requires no changes to consumers
:   Implement `PersistenceBackend` + all repository protocols. Existing application code works unchanged.

Same entity models everywhere
:   Repositories accept and return the existing frozen Pydantic models (`Task`, `CostRecord`, `Message`). No ORM models or data transfer objects.

Async throughout
:   All repository methods are async, matching the framework's concurrency model.

### Multi-Tenancy

Each company gets its own database. The `PersistenceConfig` embedded in a company's `RootConfig`
specifies the backend type and connection details (e.g., a unique SQLite file path or PostgreSQL
database URL). The `create_backend(config)` factory returns an isolated `PersistenceBackend`
instance per company; no shared state, no cross-company data leakage.

```python
# One database per company -- configured in each company's YAML
company_a_backend = create_backend(company_a_config.persistence)
company_b_backend = create_backend(company_b_config.persistence)
# Each backend has independent lifecycle: connect -> migrate -> use -> disconnect
```

!!! warning "Planned"

    Runtime backend switching (e.g., migrating a company from SQLite to PostgreSQL during
    operation) is a planned future capability. The protocol-based design already supports this:
    the engine would disconnect the current backend, connect a new one with different config,
    and migrate. Implementation details (data migration tooling, zero-downtime switchover,
    connection draining) are deferred to the PostgreSQL backend implementation.

### Optional Repository Capability Extensions

Some features are meaningful only for specific backends. The persistence layer exposes these
via **optional, runtime-checkable Protocol extensions** that concrete repositories may implement
without polluting the core `PersistenceBackend` protocol. SQLite-only or Postgres-only features
belong here.

**Pattern:**

1. Define a new `@runtime_checkable` `Protocol` in `synthorg/persistence/<feature>_capability.py`.
2. The Postgres repository implements the protocol by adding the required methods to its class;
   the SQLite repository simply does not implement them.
3. Call sites use `isinstance(repo, MyCapability)` before invoking the extension methods, and
   either skip the feature or raise `HTTP 422` (`ClientException(status_code=422, ...)`) when the
   active backend doesn't support it.
4. All user-facing parameters (column names, path expressions) are validated against an
   allowlist or regex before being woven into SQL. Value-side parameters are always passed via
   psycopg placeholders, never interpolated.

**Reference implementation:** `JsonbQueryCapability` in
`src/synthorg/persistence/jsonb_capability.py` exposes `query_jsonb_contains` and
`query_jsonb_key_exists` methods on `PostgresAuditRepository`. These use the GIN-indexed
`@>` and `?` JSONB operators on `audit_entries.matched_rules`. The audit search endpoint
checks capability via `isinstance` and returns `HTTP 422` on SQLite.

This pattern keeps the base `PersistenceBackend` protocol clean while still letting specific
backends expose their unique strengths. Future capabilities (e.g. full-text search, vector
similarity, time-series window functions) should follow the same template.

### Database-Enforced Invariants

Critical invariants that cannot be violated under any deployment (including multi-instance
Postgres clusters) are enforced by **database constraint triggers** rather than in-process
application locks. The triggers are the sole source of truth; the application catches
constraint violations and maps them to domain errors.

| Invariant | Enforcement mechanism | Postgres object | SQLite object |
|-----------|----------------------|-----------------|---------------|
| At most one CEO | Partial unique index | `idx_single_ceo` | `idx_single_ceo` |
| At least one CEO | `AFTER UPDATE` constraint trigger (deferrable) | `trg_enforce_ceo_minimum` | `enforce_ceo_minimum` (BEFORE UPDATE) |
| At least one owner | `AFTER UPDATE` constraint trigger (deferrable) | `trg_enforce_owner_minimum` | `enforce_owner_minimum` (BEFORE UPDATE) |
| Unique username | Column `UNIQUE` constraint | `users_username_key` | `users.username` |

When the repo's `save()` raises a driver exception, `_classify_postgres_user_error` (or
`_classify_sqlite_user_error`) maps it to a stable constraint token and the repository raises
`ConstraintViolationError(constraint=<token>)`. The controller matches on the structural token
rather than parsing DB error messages, so the mapping is stable across driver versions.

Service-layer mutations on the `settings` table (company, departments, agents) use
**compare-and-swap optimistic concurrency** via the existing `updated_at` column and
`SettingsRepository.set(expected_updated_at=...)`. Each mutation retries once on
`VersionConflictError` and raises if the retry also fails. Retry attempts are logged via
`API_CONCURRENCY_CONFLICT` for observability. This replaces the module-level `asyncio.Lock`
instances that were only safe within a single process.

---

## Procedural Memory Auto-Generation

When an agent fails a task, the engine's post-execution pipeline can automatically
generate a **procedural memory entry**: a structured "next time, do X when
encountering Y" lesson learned. This follows the
[EvoSkill](https://arxiv.org/abs/2603.02766) three-agent separation principle:
the **failed agent** does not write its own lesson; a separate **proposer LLM call**
analyses the failure.

### Pipeline

1. **Failure analysis payload** (`FailureAnalysisPayload`): Built from
   `RecoveryResult` + `ExecutionResult`. Includes task metadata, sanitized error
   message, tool calls made, retry count, and turn count. Deliberately excludes
   raw conversation messages (privacy boundary).

2. **Proposer LLM call** (`ProceduralMemoryProposer`): A separate completion
   call with its own system prompt analyses the payload and returns a structured
   `ProceduralMemoryProposal`.

3. **Three-tier progressive disclosure**:
     - **Discovery** (~100 tokens): concise summary for retrieval ranking.
     - **Activation** (condition + action + rationale): when/what/why.
     - **Execution** (ordered steps): concrete steps for applying the knowledge.

4. **Storage**: The proposal is stored via `MemoryBackend.store()` as a
   `MemoryCategory.PROCEDURAL` entry with `"non-inferable"` tag for retrieval
   filtering.

5. **SKILL.md materialization** (optional): When `ProceduralMemoryConfig.skill_md_directory`
   is set, the proposal is also written as a portable SKILL.md file following the
   [Agent Skills](https://agentskills.io/) format for git-native versioning.

### Configuration

`ProceduralMemoryConfig` (nested in `CompanyMemoryConfig.procedural`) controls:

- `enabled`: Toggle auto-generation on/off (default: `True`).
- `model`: Model identifier for the proposer LLM call (default: `"example-small-001"`).
- `temperature`: Sampling temperature (default: `0.3`).
- `max_tokens`: Token budget for the proposer response (default: `1500`).
- `min_confidence`: Discard proposals below this threshold (default: `0.5`).
- `skill_md_directory`: Optional path for SKILL.md file materialization.

### Integration Point

`AgentEngine._try_procedural_memory()` runs after error recovery in
`_post_execution_pipeline`. It is non-critical: failures are logged at WARNING
and never block the execution result.

### Capture Strategies

The capture system is extended beyond failure-only via pluggable ``CaptureStrategy``
implementations in ``memory/procedural/capture/``:

| Strategy | When it fires | Output |
|----------|--------------|--------|
| ``FailureCaptureStrategy`` | ``recovery_result is not None`` | Wraps existing proposer pipeline |
| ``SuccessCaptureStrategy`` | Successful completion with quality above threshold | ``"success-derived"`` tagged memory |
| ``HybridCaptureStrategy`` | Both failure and success paths | Delegates based on outcome |

``SuccessMemoryProposer`` (``memory/procedural/success_proposer.py``) provides a lighter
LLM analysis for successful executions, focusing on reusable strategies rather than
failure lessons.

Configuration via ``CaptureConfig``: ``type`` discriminator (``"failure"``/``"success"``/
``"hybrid"``), ``min_quality_score`` (default 8.0), ``success_quality_percentile`` (default
75.0).

### Pruning Strategies

Procedural memory pruning is handled by pluggable ``PruningStrategy`` implementations
in ``memory/procedural/pruning/``:

| Strategy | Method |
|----------|--------|
| ``TtlPruningStrategy`` | Remove entries older than ``max_age_days`` (default 90) |
| ``ParetoPruningStrategy`` | Multi-dimensional Pareto frontier (relevance + recency) down to ``max_entries`` |
| ``HybridPruningStrategy`` | TTL first (remove expired), then Pareto on remaining |

### Cross-Agent Propagation

Procedural memories can be propagated across agents via pluggable ``PropagationStrategy``
implementations in ``memory/procedural/propagation/``:

| Strategy | Scope | Tag |
|----------|-------|-----|
| ``NoPropagation`` | Agent-local only (safe default) | - |
| ``RoleScopedPropagation`` | Agents with same role | ``"propagated:{source_agent_id}"`` |
| ``DepartmentScopedPropagation`` | Agents in same department | ``"propagated:{source_agent_id}"`` |

All propagation strategies respect ``max_propagation_targets`` (default 10) and exclude
the source agent.

### Cross-Agent Skill Pool (Stage 3)

Organization-wide shared skills extend procedural memory with an `ORG` scope.

**`ProceduralMemoryScope` enum**: `AGENT` (per-agent private), `ROLE`,
`DEPARTMENT`, `ORG` (organization-wide shared pool).

**Extended `ProceduralMemoryProposal`** adds fields for org-scope lifecycle:

- `scope: ProceduralMemoryScope`: distribution scope
- `supersedes: tuple[NotBlankStr, ...]`: IDs of entries this supersedes
- `superseded_by: NotBlankStr | None`: tombstone marker (filtered from retrieval)
- `application_count: int`: how many times applied
- `last_applied_at: AwareDatetime | None`: last application timestamp

**`AutonomousSkillEvolver`** runs on the consolidation schedule:

1. Collects trajectories across all agents in a window via `TrajectoryAggregator`
2. Groups by error category or tool call sequence
3. Filters patterns seen by >= `min_agents_for_pattern` distinct agents
4. Builds org-scope proposals with confidence proportional to failure rate
5. Checks supersession against existing org entries (FULL/PARTIAL/CONFLICT)
6. Emits proposals as `ApprovalItem` entries for human review

**Proposal-only, structurally enforced**: `EvolverConfig.requires_human_approval`
is `Literal[True]` and cannot be set to `False`. The evolver has no write access
to org memory. Proposals land in the existing `ApprovalItem` queue.

**Supersession rules** (checked before proposal emission):

| Verdict | Condition | Action |
|---------|-----------|--------|
| CONFLICT | High condition overlap + low action similarity | Skipped, escalated to human |
| FULL | Condition superset + compatible action + higher confidence | Supersedes existing (post-approval) |
| PARTIAL | Everything else | Both coexist |

CONFLICT is checked before FULL to prevent contradictory actions from
being accepted as supersessions.

**`EvolverConfig` safety rails**: `enabled` (default False, opt-in),
`min_confidence_for_org_promotion` (0.8), `min_agents_seen_pattern` (3),
`max_proposals_per_cycle` (10), `max_org_entries` (10000, reserved for
future pruning).

**Observability**: `SKILL_EVOLVER_CYCLE_START`, `SKILL_EVOLVER_CYCLE_COMPLETE`,
`SKILL_EVOLVER_CYCLE_FAILED`, `SKILL_EVOLVER_PROPOSAL_EMITTED`,
`SKILL_EVOLVER_CONFLICT_DETECTED`, `ORG_SKILL_SUPERSEDED`, `SKILL_EVOLVER_DISABLED`.

`EvolverReport` is consumed by R3 #1265 eval loop.

---

## Memory Injection Strategies

Agent memory reaches agents through pluggable injection strategies behind the
`MemoryInjectionStrategy` protocol. The strategy determines *how* memories are surfaced to
the agent during execution.

=== "Context Injection (Default)"

    Pre-retrieves relevant memories before execution, ranks by relevance and recency, enforces
    a token budget, and formats memories as `ChatMessage`(s) injected between the system prompt
    and task instruction. The agent passively receives memories.

    **Pipeline (Linear, single-source, default):**

    1. `MemoryBackend.retrieve()`: fetch candidate memories (dense vector search)
    2. Rank by relevance + recency via linear combination
    3. Filter by `min_relevance` threshold
    4. Apply `MemoryFilterStrategy` ([Decision Log](../architecture/decisions.md) D23, optional): exclude inferable content (fails **closed** on filter exceptions: returns empty to avoid bypassing privacy filters)
    5. **Optional MMR diversity re-ranking** when `diversity_penalty_enabled: true`,
       balancing relevance vs redundancy via Maximal Marginal Relevance with
       word-bigram Jaccard similarity (see **Diversity Re-ranking** below).
       Filtering runs first so excluded entries do not act as MMR anchors and
       suppress diverse-but-visible candidates.
    6. Greedy token-budget packing
    7. Format as `ChatMessage` (configured role: SYSTEM or USER) with delimiters

    **Pipeline (RRF hybrid search, multi-source):**

    When `fusion_strategy: rrf` is configured, the pipeline runs both dense and BM25 sparse
    search in parallel and fuses results:

    1. Dense search: `MemoryBackend.retrieve()` for personal, `SharedKnowledgeStore.search_shared()` for shared (in parallel)
    2. Sparse BM25 search: `MemoryBackend.retrieve_sparse()` for personal (shared sparse disabled until `SharedKnowledgeStore` adds the method)
    3. Fuse via `fuse_ranked_lists()` with configurable `rrf_k` smoothing constant
    4. Post-RRF `min_relevance` filter on `combined_score`
    5. Apply `MemoryFilterStrategy` (optional, fails closed)
    6. **Optional MMR diversity re-ranking** when `diversity_penalty_enabled: true`
    7. Greedy token-budget packing
    8. Format as `ChatMessage`

    BM25 sparse vectors are stored alongside dense vectors in Qdrant using a named sparse
    vector field with `Modifier.IDF` (Qdrant applies IDF server-side). The `BM25Tokenizer`
    uses murmurhash3 for vocabulary-free token-to-index mapping; only term frequencies are
    stored. Sparse search is opt-in via `Mem0BackendConfig.sparse_search_enabled`.

    Shared memories (from `SharedKnowledgeStore`) are fetched in parallel, merged with personal
    memories (no `personal_boost` for shared), and ranked together.

    **Ranking Algorithm (Linear, default):**

    1. `relevance = entry.relevance_score ?? config.default_relevance`
    2. Personal entries: `relevance = min(relevance + personal_boost, 1.0)`
    3. `recency = exp(-decay_rate * age_hours)`
    4. `combined = relevance_weight * relevance + recency_weight * recency`
    5. Filter: `combined >= min_relevance`
    6. Sort descending by `combined_score`

    **Alternative: Reciprocal Rank Fusion (RRF)**

    When `fusion_strategy: rrf` is configured, multiple pre-ranked lists (e.g., from different
    retrieval sources) are merged via RRF: `score(doc) = sum(1 / (k + rank_i))` across all
    lists containing the document. Scores are min-max normalized to [0.0, 1.0]. The smoothing
    constant `k` (default 60, configurable via `rrf_k`) controls rank-difference amplification.
    RRF is the de facto standard for hybrid search fusion
    ([Qdrant](https://qdrant.tech/articles/hybrid-search/),
    [NeMo Retriever](https://huggingface.co/blog/nvidia/nemo-retriever-agentic-retrieval)). It is
    intended for multi-source scenarios (BM25 + vector, multi-round tool-based retrieval); the
    linear strategy remains the default for single-source retrieval.  Results are truncated to
    `max_results` (default 20) after scoring and sorting.

    **Diversity Re-ranking (MMR)**

    When `diversity_penalty_enabled: true` is set on the config, the
    `ContextInjectionStrategy` pipeline runs `apply_diversity_penalty()` after
    filtering and before token-budget packing.  Running the filter first ensures
    that privacy-excluded entries are not used as MMR anchors (which could
    otherwise suppress visible candidates that happen to be textually similar to
    excluded ones).  The re-ranker uses Maximal Marginal Relevance:

        MMR(candidate) = lambda * combined_score - (1 - lambda) * max_sim_to_selected

    where `diversity_lambda` (default 0.7, range `[0.0, 1.0]`) controls the
    trade-off: `1.0` = pure relevance (no diversity penalty), `0.0` = maximum
    diversity.  The default similarity function is word-bigram Jaccard; callers
    can inject a custom `similarity_fn` (e.g., cosine on embeddings) for
    domain-specific redundancy measures.  Bigram sets are pre-computed once per
    entry to keep complexity at `O(n**2)` rather than `O(n**2 * k)`.  When
    diversity is enabled, the backend over-fetches by a configurable
    `candidate_pool_multiplier` (default 3x, range 1--10) so MMR can promote
    diverse candidates that would otherwise fall below the top-K cutoff.  This
    feature applies only to `ContextInjectionStrategy`; a `model_validator`
    warns when `diversity_penalty_enabled=True` is combined with a strategy
    that ignores it (e.g. `TOOL_BASED`).

    !!! tip "Non-Inferable Filter"

        Retrieved memories are filtered before injection to exclude content the agent can
        discover by reading the codebase or environment. Only non-inferable information is
        injected: prior decisions, learned conventions, interpersonal context, historical
        outcomes. [Research](https://arxiv.org/abs/2602.11988) shows generic context increases
        cost 20%+ with minimal success improvement; LLM-generated context can actually reduce
        success rates.

        **Filter strategy ([Decision Log](../architecture/decisions.md) D23):** Pluggable `MemoryFilterStrategy` protocol. Initial
        implementation uses tag-based filtering at write time. A `non-inferable` tag convention
        with advisory validation at the `MemoryBackend.store()` boundary warns on missing tags
        but never blocks. The system prompt instructs agents what qualifies as non-inferable:
        design rationale, team decisions, "why not X," cross-repo knowledge. Uses existing
        `MemoryMetadata.tags` and `MemoryQuery.tags`; zero new models needed.

=== "Tool-Based Retrieval"

    The agent has `recall_memory` / `search_memory` tools it calls on-demand during execution.
    The agent actively decides when and what to remember. More token-efficient (only retrieves
    when needed) but consumes tool-call turns and requires agent discipline to invoke.

    Implemented via `ToolBasedInjectionStrategy`. The strategy:

    - Injects a brief system instruction about available memory tools
    - Exposes `search_memory` and `recall_memory` (by ID) tools
    - Delegates `search_memory` requests to `MemoryBackend.retrieve()` (dense-only;
      hybrid dense+sparse with RRF fusion is not yet wired into the tool-based path)
    - Hybrid retrieval and RRF fusion are handled at the `ContextInjectionStrategy`
      level, not within `ToolBasedInjectionStrategy`
    - When `query_reformulation_enabled: true` is set on the config and both a
      `QueryReformulator` and a `SufficiencyChecker` are provided at construction,
      `search_memory` runs an iterative **Search-and-Ask** loop: retrieve -> check
      sufficiency -> reformulate query -> re-retrieve, up to `max_reformulation_rounds`
      rounds (default 2, max 5).  Results from all rounds are merged by entry ID,
      keeping the highest-relevance version of any duplicate.  Sufficiency checker
      and reformulator failures degrade gracefully to the current cumulative entries
      rather than propagating.  Diversity (MMR) re-ranking is currently applied only
      in the `ContextInjectionStrategy` pipeline, not in the tool-based handler.

    **ToolRegistry integration**: `SearchMemoryTool` and `RecallMemoryTool` are `BaseTool`
    subclasses (defined in the `memory/tools/` package) that delegate execution to
    `ToolBasedInjectionStrategy.handle_tool_call()`.  The `registry_with_memory_tools()`
    factory augments a `ToolRegistry` with these tools when the strategy is
    `ToolBasedInjectionStrategy`.  `AgentEngine` accepts an optional
    `memory_injection_strategy` parameter and wires the tools into each agent's registry
    at execution time.  This ensures memory tools participate in the standard `ToolInvoker`
    dispatch pipeline, including permission checking (`ToolCategory.MEMORY`), security
    interceptors, and invocation tracking.

    **MCP bridge evaluation**: Both context injection and tool-based strategies hold direct
    `MemoryBackend` references and run in-process. The memory hot path already bypasses MCP
    by design; no additional optimization needed.

=== "Self-Editing Memory"

    The agent has three structured memory blocks (core, archival, and recall) it reads AND
    writes during execution via dedicated tools. Core memory (SEMANTIC category, tagged ``"core"``)
    is always injected into the system prompt. Archival and recall memories are tool-searched on
    demand. Six tools are provided: ``core_memory_read``, ``core_memory_write``,
    ``archival_memory_search``, ``archival_memory_write``, ``recall_memory_read``,
    ``recall_memory_write``.

    Implemented via ``SelfEditingMemoryStrategy``. Token overhead is ~250--650 tokens per session
    (2--10 writes + 5--15 searches). Best suited for long-running, high-autonomy agents (>20 turns)
    where explicit memory management reduces "forgotten context" errors. ``SelfEditingMemoryConfig``
    controls core token budget, archival search limit, per-category write access, and a safety
    valve (``allow_core_writes: bool``) for restricting core memory edits on locked-down agents.

### MemoryInjectionStrategy Protocol

All strategies implement `MemoryInjectionStrategy`:

```python
class MemoryInjectionStrategy(Protocol):

    async def prepare_messages(
        self, agent_id: NotBlankStr, query_text: NotBlankStr, token_budget: int
    ) -> tuple[ChatMessage, ...]: ...

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]: ...

    @property
    def strategy_name(self) -> str: ...
```

Strategy selection via config: ``memory.retrieval.strategy: context | tool_based | self_editing``

## Memory Service Layer

`MemoryService` (at `src/synthorg/memory/service.py`) is the single entry point for `/memory/fine-tune/*` REST endpoints and the MCP memory tools. Controllers and handlers never reach into `app_state.persistence.*` directly; the service owns the repository handle, audit logging, and typed error routing.

### Fine-tune lifecycle

`MemoryService` exposes the full fine-tune lifecycle as typed async methods:

- `start_fine_tune(plan: FineTunePlan) -> FineTuneRun`: starts a new pipeline from a `FineTunePlan`.
- `resume_fine_tune(run_id: NotBlankStr) -> FineTuneRun`: resume a previously failed or cancelled run.
- `get_fine_tune_status(run_id: NotBlankStr | None = None) -> FineTuneStatus`: snapshot of the active (or a specific) run.
- `cancel_fine_tune() -> str | None`: cancel the currently active run (destructive). Returns the cancelled run id (captured **before** cancel so the audit log can attribute it) or `None` if no run was active.
- `run_preflight(plan: FineTunePlan) -> PreflightResult`: local-env sanity check (source dir, output dir writability, override bounds).
- `list_runs(*, limit: int, offset: int) -> tuple[tuple[FineTuneRun, ...], int]`: paged historical runs + total count.
- `get_active_embedder() -> ActiveEmbedderSnapshot`: frozen snapshot of the active provider / model / checkpoint id from settings.
- `rollback_checkpoint(checkpoint_id: NotBlankStr) -> CheckpointRecord`: atomic swap of the active embedder back to *checkpoint_id* (destructive). The rollback-step helper logs a distinct `MEMORY_CHECKPOINT_ROLLBACK_FAILED` event if any intermediate step fails so operators can distinguish partial-rollback from the primary deploy failure.

Destructive entries (`cancel_fine_tune`, `rollback_checkpoint`, and `delete_checkpoint` at the handler layer) are gated by the standard MCP guardrail triple (`actor`, literal `confirm=True`, non-blank `reason`) and emit `MCP_DESTRUCTIVE_OP_EXECUTED` with the resolved actor, reason, and `target_id` (the cancelled run id or the rolled-back / deleted checkpoint id).

`FineTunePlan` is an MCP-facing Pydantic model (`src/synthorg/memory/fine_tune_plan.py`) that mirrors the runner's internal `FineTuneRequest` field-for-field but isolates the public contract from runner internals. A `@model_validator` rejects parent-directory traversal, backslashes, and Windows drive letters on `source_dir` / `output_dir` before the runner's subprocess or container mount could expose the host filesystem.

### BackendUnsupportedError routing

Fine-tune orchestration is SQLite-backed. On a persistence backend that does not expose `fine_tune_runs` / `fine_tune_checkpoints`, the service raises a typed `BackendUnsupportedError` (`domain_code = "not_supported"`, frozen with `__slots__ = ("reason",)`) instead of a generic `NotImplementedError`. MCP handlers catch it and forward through the standard `not_supported()` envelope, which emits `MCP_HANDLER_NOT_IMPLEMENTED` at WARNING; distinct from `MCP_HANDLER_CAPABILITY_GAP` (handler wired, primitive method missing) and `MCP_HANDLER_SERVICE_FALLBACK` (legacy helper, zero call sites). REST controllers map it to HTTP 501 with the same domain code.

The typed error keeps the "which gap" question resolvable without string-matching exception messages: backend-unsupported is always exactly one error class and one emitted event.
