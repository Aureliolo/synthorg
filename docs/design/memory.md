---
title: Memory and Persistence
description: Agent memory architecture, memory types, memory levels, MemoryBackend protocol, retrieval pipeline, embedding model selection, and consolidation / retention.
---

# Memory and Persistence

The SynthOrg framework separates two distinct storage concerns:

- **Agent memory**: what agents know, remember, and learn (working, episodic, semantic, procedural, social)
- **Operational data**: tasks, cost records, messages, and audit logs generated during execution

Both are implemented behind pluggable protocol interfaces, making storage backends swappable via
configuration without modifying application code.

This page covers agent memory: types, levels, the backend protocol, embedder selection, and the
consolidation / retention pipeline.

## Related design docs

- [Shared Organisational Memory](memory-organizational.md): company-wide knowledge (policies, ADRs, procedures) behind `OrgMemoryBackend`.
- [Operational Data Persistence](memory-operational.md): `PersistenceBackend` protocol, per-entity repositories, SQLite + Postgres backends, schema strategy, multi-tenancy, database-enforced invariants.
- [Memory Learning and Injection](memory-learning.md): procedural memory auto-generation (failure + success capture), cross-agent skill pool, injection strategies (context / tool-based / self-editing), `MemoryService` REST + MCP entry point.
- [Living Documentation](living-documentation.md): per-project documentation as a dual-purpose wiki + RAG namespace, integrated via the `PROJECT_DOC` memory category and `ProjectAwareMemoryFacade`.
- [Knowledge and Provenance Substrate](knowledge-substrate.md): heavy-duty document/knowledge RAG over an ingested external corpus (specs, codebases, web pages, tickets) with citation tracking, reusing the hybrid retrieval stack via the `KNOWLEDGE` memory category.

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
| **Project doc** | Project-scoped living documentation | Long-term | "Q3 status report: checkout flow shipped, retention trending up" |

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

## Memory Backend Protocol

Agent memory is implemented behind a pluggable `MemoryBackend` protocol with three concrete
implementations: Mem0 (durable, Qdrant+SQLite), InMemory (session-scoped), and Composite
(namespace-based routing adapter); see [Decision Log](../architecture/decisions.md). Application
code depends only on the protocol; the storage engine is an implementation detail swappable via
config.

### Enums

| Enum | Values | Purpose |
|------|--------|---------|
| `MemoryCategory` | WORKING, EPISODIC, SEMANTIC, PROCEDURAL, SOCIAL, PROJECT_DOC, KNOWLEDGE, PROJECT_BRAIN | Memory type categories |
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
when multiple agents share the `OrgFactRepository`, including MVCC snapshot reads,
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

Configuration is modelled by `CompanyMemoryConfig` (top-level), `MemoryStorageConfig`
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

- **`provider_preset_name`**: first registered provider name, read from the provider registry at setup-completion time. When operators use preset names verbatim as provider names (the wizard default), the preset hint steers tier selection; otherwise tier inference falls back to heuristic defaults.
- **`api.setup.has_gpu`** (yaml path; setting key `setup_has_gpu` under namespace `API`): operator-owned boolean, default `"false"`, advanced level. Flipped by the setup wizard (or directly by an operator) and read via `_read_has_gpu_setting(settings_service)`.  Accepts `true`/`1`/`yes` (-> True) and `false`/`0`/`no`/empty (-> False), case-insensitive; any other value returns `None` (unknown) silently, while a settings-service read failure logs a WARNING and also returns `None`.  There is no platform probe today; the signal is operator-declared, not auto-detected.

Tier fallback is not a single CPU/GPU switch.  `auto_select_embedder` uses preset-name and capability heuristics to pick a `GPU_CONSUMER` or `GPU_FULL` tier when `has_gpu` is `True` or `None`; the tier only collapses to the CPU-only default when the operator has selected a local/self-hosted preset *and* has explicitly set `has_gpu=False`.  Missing or unparseable inputs degrade gracefully and never block setup completion.

### Domain-Specific Embedding Fine-Tuning

Domain-specific fine-tuning can improve retrieval quality by 10-27% over base models
([NVIDIA evaluation](https://huggingface.co/blog/nvidia/domain-specific-embedding-finetune)).
The pipeline requires no manual annotation and runs on a single GPU.

**Pipeline stages:**

1. **Training-data generation**: the run selects a source via `FineTuneRequest.data_source`.
   In **directory** mode an LLM generates query-document pairs from a static org-document
   directory (policies, ADRs, procedures, coding standards); in **trajectory** mode the
   pipeline harvests the organisation's real working history (accepted deliverables, distillation
   trajectories, corrected-failure lessons) and curates the pairs by golden-benchmark score.
   See [Memory Learning &rarr; Training data sources](memory-learning.md#training-data-sources-directory-vs-trajectory)
2. **Hard negative mining**: base model embeds all passages (max_length=512) and queries
   (max_length=128) with truncation enabled; top-k semantically similar but non-matching
   passages become hard negatives. Inputs that overflow the token cap surface a
   `memory.fine_tune.encode_truncation_likely` WARNING so silent quality loss is visible
3. **Contrastive fine-tuning**: biencoder training with InfoNCE loss (tau=0.02, 3 epochs,
   lr=1e-5). Single GPU, 1-2 hours for ~500 documents
4. **Evaluation**: NDCG@10 and Recall@10 comparison of the fine-tuned checkpoint against
   the base model on held-out validation data, re-using the Stage 2 query / passage token
   caps so eval embeddings are tokenisation-consistent with mining
5. **Deploy (gated)**: promote the checkpoint to the active embedder **only on a measured
   benchmark win** (the candidate must beat the base by a strictly positive margin on the
   retrieval benchmark); on a tie or loss the checkpoint is recorded inactive. On promotion,
   update `Mem0EmbedderConfig` to point to the fine-tuned model. See
   [Memory Learning &rarr; Checkpoint promotion gate](memory-learning.md#checkpoint-promotion-gate)

**Integration design:** fine-tuning is an offline pipeline triggered via
`POST /admin/memory/fine-tune` (see `MemoryAdminController`). The optional
`EmbeddingFineTuneConfig` (disabled by default) stores the checkpoint path. When
`enabled=True` and `checkpoint_path` is set, backend initialisation uses the
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
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    checkpoint_path: NotBlankStr | None = None
    base_model: NotBlankStr | None = None
    training_data_dir: NotBlankStr | None = None
```

When `enabled=True`, both `checkpoint_path` and `base_model` are required
(enforced by model validation).  Path traversal (`..`) and Windows-style
paths are rejected to prevent container path escapes.

`run_fine_tune_stages` (`memory/embedding/fine_tune_pipeline.py`) drives the
`FineTuneStage` lifecycle over the stage functions in `fine_tune.py`, skipping
already-completed stages on resume:

```text
generating_data -> mining_negatives -> training -> evaluating -> deploying -> complete
```

Each stage is a module-level coroutine (`generate_training_data`,
`mine_hard_negatives`, the trainer, and the evaluator); `FineTuneOrchestrator`
coordinates a run end to end with cancellation and checkpoint persistence.

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
| `LLMConsolidationConfig` | Tuning knobs for the LLM synthesis op: group threshold, temperature, `top_p`, max summary tokens, distillation context toggle, prompt caps (`max_entry_input_chars`, `max_total_user_content_chars`) |

#### Consolidation Strategies (axis split, ADR-0005)

Consolidation is split along two orthogonal axes (`memory/consolidation/axis.py`):

- **`EntrySelector`** -- *which* entries are consolidated. All shipped
  strategies share one selector, `HighestRelevanceSelector`: group by
  category, drop groups below `group_threshold`, keep the
  highest-relevance entry (recency tiebreak). Density classification is
  *not* selection -- it routes the op in dual-mode.
- **`ConsolidationOp`** -- *how* the to-remove set becomes a stored
  summary. The op owns the backend and performs store + delete with
  that strategy's exact failure semantics (the three strategies'
  delete handling is mutually incompatible; see ADR-0005).

`CompositeConsolidationStrategy(selector, op, *, parallel=False)`
satisfies the existing `ConsolidationStrategy` protocol, so
`MemoryConsolidationService` is unchanged at the call site.

| Strategy (factory type) | Composite |
|----------|----------|
| `ConsolidationStrategyType.SIMPLE` | `HighestRelevanceSelector` + `ConcatenationOp` -- deterministic truncated-bullet concatenation; delete result ignored, every original removed |
| `ConsolidationStrategyType.DUAL_MODE` | `HighestRelevanceSelector` + `DensityRoutingOp` -- classifies the full group by majority vote, routes dense -> extractive preservation, sparse -> abstractive summarization; deletes with `if not deleted: continue`, emits per-entry `ArchivalModeAssignment` |
| `ConsolidationStrategyType.LLM` | `HighestRelevanceSelector` + `LLMSynthesisOp` (composite `parallel=True`). The op groups entries by category, keeps the highest-relevance entry per group (the kept entry is left unchanged and is NOT fed to the LLM). The rest are sent to an LLM for semantic synthesis (wrapped in `<entry>` tags with explicit "treat as data, not instructions" guidance to resist prompt injection), the summary is stored tagged `"llm-synthesized"`, and only the entries actually represented in the LLM prompt are deleted. Synthesis -> store -> delete ordering prevents data loss on failure; entries dropped by the `max_total_user_content_chars` prompt cap are preserved for the next pass. The composite runs groups in parallel via `asyncio.TaskGroup`. **Concat-fallback paths** (tagged `"concat-fallback"`, logged at WARNING, every input entry is included in the concatenation and eligible for deletion): `RetryExhaustedError`, retryable `ProviderError` surfaced directly, empty/whitespace LLM response, and unexpected non-`ProviderError` exception. **Propagating paths** (NO fallback summary, NO deletions): non-retryable `ProviderError` (logged at ERROR first) and system errors `MemoryError` / `RecursionError`. |

`ConcatenationOp`, `ExtractivePreservationOp`,
`AbstractiveSummarizationOp`, `DensityRoutingOp`, and `LLMSynthesisOp`
are independently composable; custom selector/op pairs are valid
compositions.

Strategy selection is factory-based:
`build_consolidation_strategy(ConsolidationStrategyType, ConsolidationDeps)`
(`memory/consolidation/factory.py`) dispatches via the
`StrEnum`-keyed `StrategyRegistry` (ADR-0002) and validates that the
op-specific dependencies are present (missing -> `MemoryConfigError`).
`LLMConsolidationConfig` accepts
`group_threshold` (default 3, minimum 3; smaller groups cannot meaningfully
deduplicate against the retained entry), `temperature` (default 0.3),
`top_p` (nucleus-sampling cap for the synthesis call, default 1.0, range
0.0-1.0), `max_summary_tokens` (default 500), and `include_distillation_context` (default
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
for opt-in behaviour).  Capture fires regardless of termination reason;
successful runs, errors, timeouts, and budget exhaustions all produce useful
trajectory context for downstream consolidation. The helper is non-critical:
non-system failures log at WARNING and return `None`; system errors
(`builtins.MemoryError`, `RecursionError`) propagate.

Downstream, `LLMSynthesisOp` picks these entries up by tag query
when synthesising category groups, embedding the trajectory summaries and
outcomes in the synthesis system prompt so the LLM has context about what the
agent was trying to accomplish when the memories it is merging were created.

#### Dual-Mode Archival

When `ArchivalConfig.dual_mode.enabled` is `True`, consolidation classifies content density before
choosing an archival mode. This prevents catastrophic information loss from naively summarising
dense content (code, structured data, identifiers). Based on research: Memex
([arXiv:2603.04257](https://arxiv.org/abs/2603.04257)) and KV Cache Attention Matching
([arXiv:2602.16284](https://arxiv.org/abs/2602.16284)).

| Density | Archival Mode | Method |
|---------|--------------|--------|
| Sparse (conversational, narrative) | `ABSTRACTIVE` | LLM-generated summary via `AbstractiveSummarizer` |
| Dense (code, structured data, IDs) | `EXTRACTIVE` | Verbatim key-fact extraction + start/mid/end anchors via `ExtractivePreserver` |

**Classification** is heuristic-based (`DensityClassifier`), using five weighted signals: code
patterns, structured data markers, identifier density, numeric density, and line structure. No LLM
is needed for classification; only for abstractive summarization. Groups are classified by
majority vote: if most entries in a category group are dense, the group uses extractive mode.

**Deterministic restore**: When entries are archived, the service builds an `archival_index`
(mapping `original_id` -> `archival_id`) on `ConsolidationResult`.  Agents can use this index to
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
