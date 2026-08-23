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

**Storage Backend:** `sqlvector` (durable; pgvector on Postgres, sqlite-vec on
SQLite), `inmemory` (ephemeral, discouraged), `composite` (namespace routing).
See [Decision Log](../architecture/decisions.md).

Each agent maintains its own memory store. The storage backend is selected via configuration
and all access flows through the [`MemoryBackend`](#memorybackend-protocol) protocol.

### Why memory lives in the operational database

Agent memory is stored in the same Postgres or SQLite database as everything
else, rather than in a dedicated vector service. That keeps one thing to run,
back up and migrate, and it lets tag filtering, expiry, and agent scoping be
plain SQL rather than predicates re-implemented in application code because the
store cannot express them.

The lexical arm uses an ordinary inverted-index table (`memory_entry_terms`)
scored by shared BM25 code, not FTS5 or `tsvector`. An ordinary table is
portable, so the two backends are held to one behavioural contract by the
conformance suite instead of being two implementations that merely resemble
each other, and ranking stays beside the RRF and MMR code that already exists.

!!! warning "pg_search is AGPL"
    ParadeDB's `pg_search` is the usual answer for BM25 on Postgres and is
    **AGPL-3.0**. The Licence Compatibility rule bars it. Use core
    `tsvector`/`pg_trgm` or the inverted-index table.

### Retrieval

Retrieval is two-stage, which is the consistent finding across the IR
literature:

1. **Recall wide** from two orthogonal signals: dense vector similarity and
   BM25 over the inverted index, fused by Reciprocal Rank Fusion
   ([Cormack et al., SIGIR 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)).
   RRF operates on ranks, so it sidesteps the score-normalisation problem that
   makes a weighted sum of cosine distance and BM25 unreliable. Each arm is
   over-fetched before fusion so a document ranked mid-list by one signal can
   still reach the fused top-k on the strength of the other.
2. **Narrow** to a small injected set, because more retrieved context is not
   better (see [Context budget](#context-budget)).

!!! danger "Dense KNN always returns something"
    A vector index returns its k nearest neighbours regardless of whether any
    of them are relevant: a nonsense query against a store holding one memory
    still returns that memory. Worse, RRF min-max normalises, so the top fused
    hit scores exactly `1.0` however irrelevant it is, which makes any
    threshold on the fused score meaningless.

    Two guards sit in the backend: a query whose embedding carries no signal
    skips the dense arm entirely, and dense hits no closer than orthogonal to
    the query are dropped (with normalised embeddings, orthogonal sits at
    `1/(1+sqrt(2)) ~= 0.414`, so the floor is a geometric statement rather
    than a tuned number).

    Neither is a calibrated relevance gate. Raw similarity is a poor binary
    judge of whether a memory will actually help; an applied study measured its
    ability to predict that at AUC 0.50, no better than chance. Abstention is a
    first-class success case.

#### A retriever honours its settings or refuses them

`MemoryRetrievalConfig.retriever` selects between a flat retriever and the
hierarchical one, and three fields (`max_workers_per_query`,
`reflective_retry_enabled`, `max_retry_count`) exist only for the second: the
flat path has no supervisor to fan out, retry against, or bound. Setting one of
them under `retriever: flat` is therefore refused at construction, not warned
about.

That is the general rule stated once. A setting an operator writes, that the
system persists, that the dashboard shows back, and that nothing then applies is
worse than one that never existed: it reads as configured. The two shapes are
the only honest ones, and this is a configuration a retriever cannot honour, so
it fails loud rather than accepting a value it will ignore. Making the flat path
honour them is the alternative that was rejected: it would mean importing the
whole supervisor apparatus, which is a different retrieval architecture, not a
setting.

The refusal is judged on VALUE, never on presence. A `model_dump()` /
`model_validate()` round-trip marks every field as explicitly set, so a
presence test refuses a config nobody touched: that is exactly the defect it
replaced, where one field's warning fired fifty times in a single run against a
value equal to its own default. A field equal to its default was not configured,
whatever `model_fields_set` says about how it arrived.

Where this can fire is bounded: neither `retriever` nor the three fields is a
live setting, so no operator write reaches it. It is the static company-config
at boot, and a failure there is booked by the subsystem reconciler as a failed
activation, reported by `GET /subsystems`, and retried on the next pass, never
a crash.

### Context budget

Injected memory competes with the prompt, and past a point it actively harms
accuracy:

- **Lost in the middle** ([Liu et al., TACL 2024](https://arxiv.org/abs/2307.03172)):
  accuracy is U-shaped in the position of the relevant item, even in
  long-context models.
- **Context rot** ([Chroma, 2026](https://www.trychroma.com/research/context-rot)):
  degradation is non-uniform and each added distractor compounds the loss. The
  mechanism is architectural, so larger windows do not fix it.

So injection is capped by `engine.memory_context_token_budget` (default 2000,
read per task so an operator change applies without a restart), and injected
entries are placed adjacent to the system prompt rather than buried
mid-context.

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
The persistence level lives on each agent's `MemoryConfig.type` (default `session`); it is not a
company-wide memory setting.

???+ note "Memory Level Configuration"

    Per agent, under the agent's identity card:

    ```yaml
    memory:
      type: "persistent"            # none | session | project | persistent (default: session)
    ```

    Company-wide, under the memory namespace:

    ```yaml
    memory:
      backend: "sqlvector"          # sqlvector (default); also composite, inmemory
      options:
        retention_days: null         # null = forever
        max_memories_per_agent: 10000
        shared_knowledge_base: true      # agents can access shared facts
      consolidation:
        interval: "daily"                # compress old memories
    ```

---

## Memory Backend Protocol

Agent memory is implemented behind a pluggable `MemoryBackend` protocol with three concrete
implementations: `SqlVectorBackend` (durable; pgvector on Postgres, sqlite-vec on SQLite),
InMemory (ephemeral, discouraged), and Composite (namespace-based routing adapter); see
[Decision Log](../architecture/decisions.md). Application
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
  backend: "sqlvector"             # sqlvector, composite, inmemory
  options:
    retention_days: null            # null = forever
    max_memories_per_agent: 10000
    shared_knowledge_base: true
  consolidation:
    interval: "daily"               # drives the consolidation scheduler

# The embedder binding is resolved at boot from the YAML override below,
# then from settings (memory.embedder_model, a provider-bound MODEL_REF,
# plus the optional memory.embedder_dims pin), which are applied last and
# so win per field. Nothing else: an unresolved binding leaves memory off
# rather than choosing a model.
```

Configuration is modelled by `CompanyMemoryConfig` (top-level), `MemoryStorageConfig`
(storage paths/backends), and `MemoryOptionsConfig` (behaviour tuning). All are frozen
Pydantic models. `create_memory_backend(config, *, deps=...)` returns an isolated
`MemoryBackend` per company; `deps` carries the vector repository from the persistence
layer and the embedder, neither of which the backend can invent for itself.

### Boot wiring and the fail-loud rule

`api/lifecycle_helpers/memory_backend_wiring.wire_memory_backend` builds the
backend and publishes it on `MemoryStateSlice`. It runs **before**
`_install_runtime_services`, so an engine constructed in the same boot can
already read it.

When no embedding model resolves, **no backend is wired** and the failure is
logged at ERROR. There is no automatic fallback to keyword-only memory:

!!! danger "Why there is no silent fallback"
    Memory previously arrived as a side effect of the training-service
    auto-wire, which published an ephemeral in-process store whose entire
    matcher was a substring test. Every consumer (agent memory, the project
    brain, the knowledge substrate, living docs) silently got keyword recall
    over a dict that emptied on restart, while the settings page advertised a
    durable backend. A store that looks like working memory but recalls the
    wrong things is worse than one that is plainly off.

The ephemeral backend remains reachable as an explicit operator choice
(`memory.backend: inmemory`) and is marked discouraged in settings. It is never
selected automatically.

#### A backend wired later still reaches agents

The engine resolves its injection strategy **live**, through
`MemoryInjectionResolver` (`workers/_memory_assembly.py`), cached on the
identity of the two backends it is built from and re-read per task. Captured
once at construction, an engine built while the embedding model was unreachable
would hold `None` for the life of the process: the reconciler wiring the backend
on a later pass would reach nothing, so every agent would keep running with no
recall and the operator's fix could not take effect without a restart. Recall
being off is a state the process is expected to leave, so nothing may hold a
snapshot of it.

The cache key covers `org_memory_backend` as well as the vector backend. The
two are separate subsystems with separate requirements, so they can come up on
different passes; keyed on the vector backend alone, an org backend that
arrived second would never reach the strategy and company-wide knowledge would
stay out of every agent's context.

Being off is also now reported rather than only logged. The reconciler
escalates a subsystem that enters `blocked` or `failed` through
`NotificationDispatcher` (HEALTH category), deduplicated per subsystem and
reason so a condition alerts once rather than once per pass. `GET /subsystems`
answers "why is this not up" for whoever asks; this is what reaches an operator
who is not asking, which is the gap that let memory stay off through a whole
working session.

### Embedding Model Selection

**The operator names the embedding model. Nothing selects one.**

`memory.embedder_model` is a `MODEL_REF`, so its type refuses a value that names
a model without the provider serving it, and the guided setup's picker offers
the connected providers' catalogue unfiltered plus the built-in embedder. There
is no ranking table, no recommendation, and no deployment-tier inference. Unset
means memory stays OFF and says so.

Selection used to be the product's job: a shipped benchmark table ranked models,
matched one against the operator's catalogue, and read its vector width from a
static column in the same table. That produced a binding whose width the vector
store could not index, on a stack whose only embedder was a Matryoshka model
that could have served any narrower width, and nobody had decided anything.

!!! danger "The built-in embedder is chosen, never fallen back to"
    `builtin/hashing` is deterministic feature hashing: it matches shared
    vocabulary, not meaning. It exists so an operator with no embedding model can
    still run, and it is reachable **only** by naming it. No path substitutes it
    for a model that failed to load, a provider that went unreachable, a missing
    optional dependency, or an unset setting, and no embedder is constructed
    inside an `except` handler. Memory quietly becoming lexical is the same
    silent-failure class as memory quietly becoming ephemeral, one layer down.
    Enforced by `check_no_silent_embedder_fallback.py`.

**The vector width is measured, not looked up.** On selection the backend embeds
a probe string through the chosen pair and counts components
(`memory/embedding/probe.py`). That is the model's own answer, so a model this
codebase has never heard of is as usable as one it has, and the call doubles as
proof the binding works at all: a model that cannot embed fails at selection
rather than at the first memory write.

The measured width is never written back. `memory.embedder_dims` is the
operator's own truncation pin, which is how a wide Matryoshka model is brought
under the index ceiling, and persisting a measurement into it would make the two
indistinguishable: a width measured for one model would outlive it and be
applied to the next as though it had been asked for, silently truncating vectors
from a model it was never measured against. Boot measures again, against
whatever model is bound then.

The width the probe reports is used as-is, including above pgvector's 4000-
dimension HNSW ceiling: those vectors are stored and searched correctly, just
without an approximate index, which the health surface reports as DEGRADED and
readiness ignores. Truncating automatically would be sound only for a Matryoshka
model, and knowing which models those are is the shipped-table approach this
section replaced.

This binding is the only embedding model the product serves retrieval from,
and nothing else is selectable: a meeting's conflict detectors score positions
with the built-in lexical embedder, chosen for that job rather than offered as
one option among several. A second locally-loaded embedder selected from
somewhere else would be a second surface for the same decision.

Fine-tuning is the one place a model is loaded locally, and where that happens
depends on the install. A Docker install runs each stage in the configured
fine-tuning image, so nothing loads into the backend process: the backend image
carries neither `torch` nor `sentence-transformers`. A bare-metal install with
those extras present and no image configured runs the stage in-process instead
(the execution-config derivation below picks between the two).

#### Why the rankings were about quality, not selection

The analysis below still holds and is why the reference page is worth reading
before choosing. It informs an operator's decision; it no longer makes one.

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
[Embedding Evaluation](../reference/embedding-evaluation.md) reference page for the full analysis
and the measured results by resource class.

Key findings:

- Larger models do not always outperform smaller ones on memory retrieval
- Dialogue/social memory is the hardest retrieval category for all models
- Instruction sensitivity varies per model; must be validated per deployment
- Results are reported for three resource classes: full-resource (7-12B),
  mid-resource (1-4B), and CPU-only (< 1B)

Those classes describe what was measured, for an operator sizing a deployment.
They are not tiers this codebase infers, and nothing reads them.

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
   update the resolved `EmbedderConfig` to point to the fine-tuned model. See
   [Memory Learning &rarr; Checkpoint promotion gate](memory-learning.md#checkpoint-promotion-gate)

**Integration design:** fine-tuning is an offline pipeline triggered via
`POST /admin/memory/fine-tune` (served by the memory sub-controllers under
`src/synthorg/api/controllers/memory/`). Promotion is decided by
`should_promote_checkpoint` (`memory/embedding/promotion.py`) from the eval
stage's NDCG@10 A/B, and a missing measurement counts as no win.

A promoted checkpoint is recorded active, and a snapshot of the embedder
settings is taken so a rollback has something to restore. `deploy_checkpoint`
deliberately does **not** repoint `memory.embedder_model` at the checkpoint:
that setting is a provider-bound model reference, so a filesystem path
written into it would reach the boot path as a model name to dispatch on.
Which embedder serves stays the operator's explicit choice, per
[Embedding model selection](#embedding-model-selection).

!!! warning "A dimension change is a re-index"
    Vectors are only comparable to each other when they came from the same
    model at the same width. Changing `embedder_dims` therefore invalidates
    every stored vector: the store provisions a fresh dimension-suffixed index
    rather than silently mixing incomparable vectors into the existing one.

The configured width also decides how Postgres stores and indexes the column,
because pgvector caps an HNSW index at 2000 dimensions for a full-precision
`vector` and 4000 for a half-precision `halfvec`. At or below 2000 the column
is exact and indexed; up to 4000 it is indexed at half precision; above that no
approximate index can be built at all, so the column is still created and dense
search still runs as an exact scan over the corpus, reported at ERROR under
`memory.dense_index.unindexable`. Recall stays semantic in every case, but an
unindexed width reads every row per query.

Because that state answers every query correctly and only costs latency, a log
line alone would never be noticed, so `/health` reports memory `DEGRADED` for
it: the backend exposes `dense_search_indexed` alongside `supports_dense_search`
precisely so "recall changed meaning" and "recall got slower" cannot collapse
into one flag. A width above pgvector's 16000-dimension storage ceiling is
refused outright rather than degraded, since no column could hold it.

Two conditions the index build reports rather than hides: an index a crashed
build left `INVALID` is dropped and a rebuild attempted (`CREATE INDEX
CONCURRENTLY IF NOT EXISTS` matches on name alone, so it would otherwise be
accepted as present forever; either the drop or the rebuild can still fail, and
readiness reports that separately), and an empty dense column left behind by an
earlier width is logged at INFO as schema drift, which the orphaned-width error
misses because it only fires when a leftover column still holds rows.

Setting `embedder_dims` *below* the model's own output width is the one
sanctioned mismatch: the embedder truncates each vector to its leading
components and renormalises, which is how a Matryoshka-trained model is used at
a smaller width and how a model wider than the index ceiling is brought under
it. Truncating a model that was not MRL-trained degrades recall, so this only
ever happens on the operator's explicit instruction, never by inference.

**Container execution:** when `FineTuneExecutionConfig.backend` is `"docker"`, each
torch-bound pipeline stage (hard-negative mining, training, evaluation) runs inside an
ephemeral one-shot `synthorg-fine-tune-gpu` (default) or `synthorg-fine-tune-cpu`
container spawned by the backend via the Docker API and removed on exit. Data
generation (which holds DB/LLM handles) and deploy/promotion (which touch settings +
persistence) always run in-process regardless of backend. Both image variants ship
the same Python runner and accept the same stage-config contract; they differ only in
the bundled torch build (CUDA ~4 GB download / ~7 GB on disk vs CPU ~1.7 GB) and
whether GPU passthrough is usable. The variant is selected at `synthorg init` time
(fresh installs) or via `synthorg config set fine_tuning_variant gpu|cpu` (post-init,
preserves data) and persisted as `fine_tuning_variant` in `config.json`. The backend
consumes `SYNTHORG_FINE_TUNE_IMAGE` verbatim as a full image reference (including
registry, repository, and either a `:tag` or a digest-pinned `@sha256:...`); in a
CLI-managed install the rendered `compose.yml` writes the verified digest-pinned ref
into this env var automatically (surfaced as the `memory.fine_tune_image` setting,
resolved DB > env > default at boot). Operators running a hand-managed `compose.yml`
without the CLI set `SYNTHORG_FINE_TUNE_IMAGE` on the backend directly; tag-based
refs work for quick evaluation, but production deployments should pin a digest so
the backend spawns the exact attested image. See
[Deployment &rarr; Fine-Tuning (optional)](../guides/deployment.md#fine-tuning-optional)
for the BYO snippet. The container reads its flat stage configuration from the
`SYNTHORG_FINE_TUNE_STAGE_CONFIG` env var (inline JSON injected by the launcher) and
emits structured markers on stdout that the launcher parses: `STAGE_START:` /
`STAGE_COMPLETE:` bracket the run, `PROGRESS:<fraction>` drives the WS progress
pipeline in the orchestrator, and `ERROR:<message>` carries the failure detail. The shared
data volume is mounted read-write at `/data` (training data in, checkpoints out
under `/data/fine-tune/runs/<run_id>/`), so consecutive stages hand off through
deterministic paths. The volume name comes from `memory.fine_tune_data_volume`
(default `synthorg-data`, the compose data volume; env override
`SYNTHORG_FINE_TUNE_DATA_VOLUME`) and must be a Docker volume NAME, never a
path. Stage containers get GPU passthrough via Docker
`DeviceRequests` when `gpu_enabled=True` (only meaningful for the GPU variant;
`memory.fine_tune_default_gpu` supplies the default for runs without an
explicit execution config), a memory limit from `memory.fine_tune_memory_limit`,
and a per-stage wall-clock
timeout from `memory.fine_tune_stage_timeout_seconds`; cancellation stops the
container (SIGTERM reaches the runner's cooperative token). When a run requests no
explicit execution config the backend derives it: image configured means docker,
no image means in-process (bare-metal installs with the torch extras installed
directly), and the effective config is baked into the persisted run for resume and
audit. Preflight boots the same image with `SYNTHORG_FINE_TUNE_PROBE=1`, which
prints one `PROBE_OK gpu=<name|none> vram_gb=<x>` / `PROBE_FAIL <reason>` line
proving the image runs and detects the GPU before a long training run starts (cached
briefly so dashboard polls do not spawn probe containers per request). There is no
standing fine-tune compose service; containers exist only while a stage or probe
runs.

Each run freezes its own configuration, so a resume replays what the run
started with rather than whatever the settings say later
(`memory/embedding/fine_tune_models.py`):

```python
class FineTuneRunConfig(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data_source: FineTuneDataSourceType = FineTuneDataSourceType.DIRECTORY
    source_dir: NotBlankStr | None = None
    base_model: NotBlankStr
    output_dir: NotBlankStr
    epochs: int = 3
    learning_rate: float = 1e-5
    temperature: float = 0.02
    top_k: int = 4
    batch_size: int = 128
    validation_split: float = 0.1
    execution: FineTuneExecutionConfig | None = None
```

Path traversal (`..`) and Windows-style paths are rejected to prevent
container path escapes.

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
