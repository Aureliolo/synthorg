---
title: Operational Data Persistence
description: PersistenceBackend protocol, per-entity repositories, SQLite + Postgres backends, schema strategy, multi-tenancy, and database-enforced invariants.
---

# Operational Data Persistence

Agent memory is handled by the `MemoryBackend` protocol (Mem0 initial, custom stack future;
see [Decision Log](../architecture/decisions.md)). **Operational data** (tasks, cost records,
messages, audit logs) is a separate concern managed by a pluggable `PersistenceBackend` protocol.
Application code depends only on repository protocols; the storage engine is an implementation
detail swappable via config.

See also: [Memory and Persistence](memory.md) (agent memory), [Memory Learning](memory-learning.md) (procedural memory auto-generation + injection strategies), [Shared Organisational Memory](memory-organizational.md).

## Architecture

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

## Protocol Design

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

## Configuration

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

## Entities Persisted

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

## Schema Strategy

- **Declared schema in `schema.sql`**, applied via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/) revision files under `persistence/<backend>/revisions/`.
- At startup, `PersistenceBackend.migrate()` calls `synthorg.persistence.migrations.migrate_apply()` which wraps yoyo in-process.
- Yoyo tracks applied versions plus content hashes in its `_yoyo_migration` table (and a per-action audit log in `_yoyo_log`); no hand-rolled version tracking.
- Both persistence and ontology tables are consolidated into a single `schema.sql` (same database file).
- CI runs `scripts/check_schema_drift_revisions.py` on every PR (declared `schema.sql` vs accumulated revisions, per backend).
- Squashing: a single-baseline squash collapses every revision into one seed file derived from `schema.sql` (run rarely; documented in `docs/guides/persistence-migrations.md`).

## Key Principles

Application code never imports a concrete backend
:   Only repository protocols are used. This ensures complete decoupling from the storage engine.

Adding a new backend requires no changes to consumers
:   Implement `PersistenceBackend` + all repository protocols. Existing application code works unchanged.

Same entity models everywhere
:   Repositories accept and return the existing frozen Pydantic models (`Task`, `CostRecord`, `Message`). No ORM models or data transfer objects.

Async throughout
:   All repository methods are async, matching the framework's concurrency model.

## Multi-Tenancy

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

## Optional Repository Capability Extensions

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

## Database-Enforced Invariants

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
