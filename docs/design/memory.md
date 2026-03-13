---
title: Memory & Persistence
description: Agent memory architecture, shared organizational memory, backend protocols, operational data persistence, and memory injection strategies.
---

# Memory & Persistence

The SynthOrg framework separates two distinct storage concerns:

- **Agent memory** -- what agents know, remember, and learn (working, episodic, semantic, procedural, social)
- **Operational data** -- tasks, cost records, messages, and audit logs generated during execution

Both are implemented behind pluggable protocol interfaces, making storage backends swappable via
configuration without modifying application code.

---

## Memory Architecture

```text
+-------------------------------------------------+
|              Agent Memory System                |
+----------+----------+-----------+---------------+
| Working  | Episodic | Semantic  | Procedural    |
| Memory   | Memory   | Memory    | Memory        |
|          |          |           |               |
| Current  | Past     | Knowledge | Skills &      |
| task     | events & | & facts   | how-to        |
| context  | decisions| learned   |               |
+----------+----------+-----------+---------------+
|            Storage Backend                      |
|   SQLite / PostgreSQL / File-based              |
|   + Mem0 (initial, implemented) / Custom (future)|
|     See Decision Log                             |
+-------------------------------------------------+
```

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
      level: "persistent"            # none, session, project, persistent (default: session)
      backend: "mem0"               # mem0, custom, cognee, graphiti (future) -- see Decision Log
      storage:
        data_dir: "/data/memory"    # mounted Docker volume path
        vector_store: "qdrant"      # qdrant (embedded), qdrant-external, etc.
        history_store: "sqlite"     # sqlite, postgresql
      options:
        retention_days: null         # null = forever
        max_memories_per_agent: 10000
        consolidation_interval: "daily"  # compress old memories
        shared_knowledge_base: true      # agents can access shared facts
    ```

---

## Shared Organizational Memory

Beyond individual agent memory, the framework provides **organizational memory** -- company-wide
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

The following backends illustrate why `OrgMemoryBackend` is a protocol -- the architecture
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
    writes are versioned and auditable. This prevents agents from corrupting shared organizational
    knowledge while allowing senior agents to document decisions.

---

## Memory Backend Protocol

Agent memory is implemented behind a pluggable `MemoryBackend` protocol (Mem0 initial, custom
stack future -- see [Decision Log](../architecture/decisions.md)). Application code depends only on the protocol; the storage engine is an
implementation detail swappable via config.

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

    async def store(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr: ...
    async def retrieve(self, agent_id: NotBlankStr, query: MemoryQuery) -> tuple[MemoryEntry, ...]: ...
    async def get(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> MemoryEntry | None: ...
    async def delete(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool: ...
    async def count(self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None) -> int: ...
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
`MemoryBackend`. Not all backends require cross-agent queries -- this keeps the base protocol
clean.

```python
@runtime_checkable
class SharedKnowledgeStore(Protocol):
    """Cross-agent shared knowledge operations."""

    async def publish(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr: ...
    async def search_shared(self, query: MemoryQuery, *, exclude_agent: NotBlankStr | None = None) -> tuple[MemoryEntry, ...]: ...
    async def retract(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool: ...
```

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

### Configuration

```yaml
memory:
  backend: "mem0"
  level: "persistent"              # none, session, project, persistent (default: session)
  storage:
    data_dir: "/data/memory"
    vector_store: "qdrant"
    history_store: "sqlite"
  options:
    retention_days: null            # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"
    shared_knowledge_base: true
```

Configuration is modeled by `CompanyMemoryConfig` (top-level), `MemoryStorageConfig`
(storage paths/backends), and `MemoryOptionsConfig` (behaviour tuning). All are frozen
Pydantic models. The `create_memory_backend(config)` factory returns an isolated
`MemoryBackend` instance per company.

### Consolidation and Retention

Memory consolidation, retention enforcement, and archival are configured via frozen Pydantic
models in `memory/consolidation/config.py`:

| Config | Purpose |
|--------|---------|
| `ConsolidationConfig` | Top-level: `max_memories_per_agent` limit, nested `retention` and `archival` sub-configs |
| `RetentionConfig` | Per-category `RetentionRule` tuples (category + retention_days), optional `default_retention_days` fallback |
| `ArchivalConfig` | Enables/disables archival of consolidated entries to `ArchivalStore` |

!!! abstract "Scope Note"

    Retention is currently per-category, not per-agent. Per-agent retention overrides are a
    scope gap to be addressed in a future iteration.

---

## Operational Data Persistence

Agent memory is handled by the `MemoryBackend` protocol (Mem0 initial, custom stack future --
see [Decision Log](../architecture/decisions.md)). **Operational data** -- tasks, cost records, messages, audit logs -- is a separate
concern managed by a pluggable `PersistenceBackend` protocol. Application code depends only on
repository protocols; the storage engine is an implementation detail swappable via config.

### Architecture

```text
+------------------------------------------------------------------+
|                     Application Code                             |
|  engine/  budget/  communication/  security/                     |
|     |        |           |             |                         |
|     v        v           v             v                         |
|  +------+ +------+ +----------+ +----------+                    |
|  | Task | | Cost | | Message  | |  Audit   |  <-- Repository    |
|  | Repo | | Repo | |  Repo    | |  Repo    |      Protocols     |
|  +--+---+ +--+---+ +----+-----+ +----+-----+                    |
|     +--------+----------+------------+                           |
|                      |                                           |
|  +-------------------+-------------------------------------------+
|  |              PersistenceBackend (protocol)                    |
|  |  connect() . disconnect() . health_check() . migrate()       |
|  +-------------------+-------------------------------------------+
|                      |                                           |
|  +-------------------+-------------------------------------------+
|  |  SQLitePersistenceBackend (initial)                           |
|  |  PostgresPersistenceBackend (future)                          |
|  |  MariaDBPersistenceBackend (future)                           |
|  +---------------------------------------------------------------+
+------------------------------------------------------------------+
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
    #     parked_contexts, audit_entries
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
  backend: "sqlite"                   # sqlite, postgresql, mariadb (future)
  sqlite:
    path: "/data/synthorg.db"       # database file path (mounted volume in Docker)
    wal_mode: true                    # WAL for concurrent read performance
    journal_size_limit: 67108864      # 64 MB WAL journal limit
  # postgresql:                       # future
  #   url: "postgresql://user:pass@host:5432/synthorg"
  #   pool_size: 10
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
| Agent runtime state (planned) | `engine/` | `AgentStateRepository` (planned) | by agent_id, active agents |

### Migration Strategy

- Migrations run programmatically at startup via `PersistenceBackend.migrate()`
- Initial migration creates all tables
- Versioned migrations implemented per-backend (e.g., `persistence/sqlite/migrations.py` for SQLite)
- SQLite uses `user_version` pragma for version tracking; PostgreSQL/MariaDB use a migrations table

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
instance per company -- no shared state, no cross-company data leakage.

```python
# One database per company -- configured in each company's YAML
company_a_backend = create_backend(company_a_config.persistence)
company_b_backend = create_backend(company_b_config.persistence)
# Each backend has independent lifecycle: connect -> migrate -> use -> disconnect
```

!!! warning "Planned"

    Runtime backend switching (e.g., migrating a company from SQLite to PostgreSQL during
    operation) is a planned future capability. The protocol-based design already supports this --
    the engine would disconnect the current backend, connect a new one with different config,
    and migrate. Implementation details (data migration tooling, zero-downtime switchover,
    connection draining) are deferred to the PostgreSQL backend implementation.

---

## Memory Injection Strategies

Agent memory reaches agents through pluggable injection strategies behind the
`MemoryInjectionStrategy` protocol. The strategy determines *how* memories are surfaced to
the agent during execution.

=== "Context Injection (Default)"

    Pre-retrieves relevant memories before execution, ranks by relevance and recency, enforces
    a token budget, and formats memories as `ChatMessage`(s) injected between the system prompt
    and task instruction. The agent passively receives memories.

    **Pipeline:**

    1. `MemoryBackend.retrieve()` -- fetch candidate memories
    2. Rank by relevance + recency (algorithm below)
    3. Filter by `min_relevance` threshold
    4. Apply `MemoryFilterStrategy` ([Decision Log](../architecture/decisions.md) D23, optional) -- exclude inferable content
    5. Greedy token-budget packing
    6. Format as `ChatMessage` (configured role: SYSTEM or USER) with delimiters

    Shared memories (from `SharedKnowledgeStore`) are fetched in parallel, merged with personal
    memories (no `personal_boost` for shared), and ranked together.

    **Ranking Algorithm:**

    1. `relevance = entry.relevance_score ?? config.default_relevance`
    2. Personal entries: `relevance = min(relevance + personal_boost, 1.0)`
    3. `recency = exp(-decay_rate * age_hours)`
    4. `combined = relevance_weight * relevance + recency_weight * recency`
    5. Filter: `combined >= min_relevance`
    6. Sort descending by `combined_score`

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
        `MemoryMetadata.tags` and `MemoryQuery.tags` -- zero new models needed.

=== "Tool-Based Retrieval (Future)"

    The agent has `recall_memory` / `search_memory` tools it calls on-demand during execution.
    The agent actively decides when and what to remember. More token-efficient (only retrieves
    when needed) but consumes tool-call turns and requires agent discipline to invoke.

=== "Self-Editing Memory (Future)"

    The agent has structured memory blocks (core, archival, recall) it reads AND writes during
    execution via dedicated tools. Core memory is always in context; archival and recall are
    searched via tools. Most sophisticated (Letta/MemGPT-inspired) but highest complexity and
    LLM overhead.

### MemoryInjectionStrategy Protocol

All strategies implement `MemoryInjectionStrategy`:

```python
class MemoryInjectionStrategy(Protocol):

    async def prepare_messages(
        self, agent_id: NotBlankStr, query_text: str, token_budget: int
    ) -> tuple[ChatMessage, ...]: ...

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]: ...

    @property
    def strategy_name(self) -> str: ...
```

Strategy selection via config: `memory.retrieval.strategy: context | tool_based | self_editing`
