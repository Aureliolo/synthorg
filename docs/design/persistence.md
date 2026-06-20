---
title: Persistence
description: Repository protocol abstraction, SQLite and Postgres backends, time-series tables, TimescaleDB hypertables, and extension strategy.
---

# Persistence

SynthOrg abstracts durable storage behind a set of repository protocols so the
engine, agent runtime, budget tracker, security auditor, and HR subsystems all
depend on interfaces rather than concrete backends. Two backends ship in the
reference implementation: SQLite for single-user development and small
self-hosted setups, Postgres for multi-user production deployments with
concurrent writers. Both implement the same Python protocol surface; switching
backends is a configuration change, not a code change.

## Persistence boundary (mandatory)

`src/synthorg/persistence/` is the **only** place in the repository that may
import `aiosqlite`, `sqlite3`, `psycopg`, or `psycopg_pool`, or emit raw SQL
DDL/DML keywords in string literals. The boundary exists so every durable
feature goes through a repository protocol, so swapping a backend is a config
change, and so encrypted secret backends and test fixtures are never skipped
by a caller that reached past the abstraction.

- Sanctioned exceptions cover three categories: (1) the two agent-facing DB
  introspection tools (`src/synthorg/tools/database/schema_inspect.py`,
  `src/synthorg/tools/database/sql_query.py`); (2) security/scanning
  utilities that inspect user-supplied SQL, such as
  `src/synthorg/security/rules/destructive_op_detector.py`, whose detection
  payload *is* DDL keyword strings; and (3) test fixtures and conformance
  harnesses that hold driver primitives for cross-subsystem setup. The
  authoritative list lives in `_ALLOWLIST` inside
  `scripts/check_persistence_boundary.py`; consult it before assuming a
  path is (or is not) covered. Any new exception must be added there with
  a justifying comment.
- Every durable feature **must** define a repository protocol in
  `persistence/<domain>_protocol.py`, ship concrete implementations under
  `persistence/{sqlite,postgres}/`, and expose them on `PersistenceBackend`.
- Adding a migration: **read
  [`docs/guides/persistence-migrations.md`](../guides/persistence-migrations.md)
  first.**  Never hand-edit a revision file that already exists on
  ``origin/main``; yoyo's content-hash check refuses to re-apply an
  edited file. Author a new revision with your delta instead.
- Per-line opt-out: append `# lint-allow: persistence-boundary -- <required justification>` as a trailing comment. The justification after the `--` separator must be non-empty.
- Enforced by `scripts/check_persistence_boundary.py`, wired into the pre-push
  hook and the CI Lint job; both fail loudly on violations.
- A complementary gate, `scripts/check_schema_drift.py` (also pre-push + CI
  Lint), validates structural parity between
  `persistence/sqlite/schema.sql` and `persistence/postgres/schema.sql` and
  between the two `revisions/` directories. Currently-tolerated drift
  (TEXT-vs-JSONB, TEXT-vs-TIMESTAMPTZ, GIN-only-on-Postgres indexes, the
  TimescaleDB composite-PK pattern) is frozen in
  `scripts/schema_drift_baseline.txt` with a one-line per-entry
  justification; the migration guide explains the baseline-update
  workflow.

The same rule is restated verbatim in [`CLAUDE.md`](../../CLAUDE.md)
(`## Persistence Boundary`) so Claude and human developers see the same contract
no matter which surface they hit first.

## Backend catalog

| Backend  | Primary use case                                    | Concurrency model                    | Migration tool |
|----------|-----------------------------------------------------|--------------------------------------|----------------|
| SQLite   | Single-user dev, small self-hosted, demos           | Single-writer with WAL journaling    | yoyo-migrations |
| Postgres | Multi-user production, high-concurrency write paths | Row-level MVCC, server-side triggers | yoyo-migrations |

Repositories live under `src/synthorg/persistence/sqlite/` and
`src/synthorg/persistence/postgres/`, behind domain-scoped protocols declared
one level up in `src/synthorg/persistence/`:

| Protocol module                        | Concerns |
|----------------------------------------|-----------|
| `protocol.py` (`PersistenceBackend`)   | Backend aggregate: connect / disconnect, migrate, and accessors for every repository below |
| `_generics.py`                         | Six generic categories (`SingletonRepository`, `IdKeyedRepository`, `FilteredQueryRepository`, `AppendOnlyRepository`, `StatefulRepository`, `MVCCRepository`) that concrete protocols compose via Protocol inheritance. See [ADR-0001](../decisions/0001-repository-protocol-consolidation.md) for the consolidation rationale and the per-entity migration inventory. |
| `approval_protocol.py`                 | `ApprovalRepository`: human-in-the-loop approval queue |
| `auth_protocol.py`                     | `SessionRepository`, `RefreshTokenRepository`, `LockoutRepository` |
| `ceremony_scheduler_state_protocol.py` | `CeremonySchedulerStateRepository`: per-sprint ceremony-trigger state (completion counters, fired-once flags, velocity history) so restarts re-hydrate position |
| `codebase_structure_map_protocol.py`   | `CodebaseStructureMapRepository`: brownfield-intake structure map (modules, entry points, tests, build files, deps) for an imported codebase (1:1 per project) |
| `escalation_protocol.py`               | Conflict-resolution escalation queue |
| `fine_tune_protocol.py`                | `FineTuneRunRepository`, `FineTuneCheckpointRepository` |
| `mcp_protocol.py`                      | MCP catalog installation repository |
| `meeting_cooldown_protocol.py`         | `MeetingCooldownRepository`: per-meeting-type last-triggered timestamp for the recurring-meeting cooldown |
| `memory_protocol.py`                   | Org-memory fact repository with MVCC log + snapshot |
| `ontology_protocol.py`                 | Ontology entity + drift-report repositories |
| `project_environment_protocol.py`      | `ProjectEnvironmentRepository`: per-project reproducible-environment provisioning cache (1:1 per project; declaration hash + type + built image ref) |
| `tracked_container_protocol.py`        | `TrackedContainerRepository`: Docker sandbox container registry so a restart can reconcile orphans on both the daemon and DB sides |
| `version_repo.py`                      | Generic version-snapshot repository reused by ontology + future versioned entities |
| `secret_backends/protocol.py`          | `SecretBackend` protocol used by the secret-backend factory |

The table above is representative, not exhaustive; the authoritative
inventory is the set of properties / factory methods on
``PersistenceBackend`` in ``src/synthorg/persistence/protocol.py``, and the
concrete repositories live alongside the dialect-specific backends in
``src/synthorg/persistence/{sqlite,postgres}/``.

New repository protocols should inherit one or more generic categories
from ``_generics.py`` per [ADR-0001](../decisions/0001-repository-protocol-consolidation.md).
Bespoke methods are permitted only under the ADR's D7 rule (a real
performance optimisation or a domain invariant callers must not bypass).

The dialect-specific subdirectories share a third sibling at
``src/synthorg/persistence/_shared/``.  This is the canonical home for
backend-agnostic serialisation, deserialisation, error-classification,
and timestamp-normalisation logic that both SQLite and Postgres repos
would otherwise duplicate (modules: ``audit.py``, ``custom_rule.py``,
``datetime_marshaller.py`` exposing the strict ISO 8601 pair
``parse_iso_utc`` / ``format_iso_utc`` plus the shared ``normalize_utc``
helper exported from ``__init__.py``, ``pagination.py``, the ``RowLike``
row protocol in ``rows.py``, and per-aggregate row<->model marshalling
modules ``charter_marshalling.py`` / ``cost_forecast_marshalling.py`` /
``org_fact_marshalling.py`` / ``workflow_definition_marshalling.py`` /
``workflow_execution_marshalling.py``).  Backend repos
pass driver-specific bits (SQL
placeholder style, JSON wrappers, predicates that classify duplicate-key
errors) into the helpers as callables, so the helpers stay portable and
the conformance tests at
``tests/conformance/persistence/test_*_repository.py`` exercise the
canonical contract on both backends in a single parametrized pass.
Adding a new shared helper: extract the duplicated logic, add a
``test_*_helpers.py`` unit suite alongside it, and add a conformance
test that runs against both backends.

Every concrete repository implements its matching protocol; application code
depends on the protocol, not the implementation. Switching backends is a
configuration change (``PersistenceConfig.backend``) rather than a code change,
and the unit-test suite stays backend-free so most tests remain fast and local.

API controllers reach persistence through **domain-scoped service layers**
(``ArtifactService``, ``WorkflowService``, ``MemoryService``,
``CustomRulesService``, ``UserService``, ...) rather than importing
repositories directly. Services keep controllers thin, centralise audit
logging, and own cross-repo orchestration (e.g. workflow-definition delete
cascading to its version snapshots) so the audit trail stays consistent
regardless of which HTTP endpoint invoked the mutation.

Postgres adds server-side integrity beyond what SQLite can express: `CONSTRAINT
TRIGGER`s enforce "exactly one CEO" and "at least one owner" invariants across
concurrent writers, and optional capability protocols surface Postgres-native
features (JSONB analytics, TimescaleDB hypertables) that SQLite callers
simply do not see.

### Cross-worker CAS on JSON-blob settings

Where runtime state is persisted as a single JSON blob in a settings key
(`coordination.dept_ceremony_policies` is the canonical example), concurrent
writers across workers cannot rely on in-process locks. Instead the controller
follows a bounded **compare-and-swap** loop:

1. `settings_service.get_versioned(namespace, key)` returns the current
   `(value, updated_at)` pair.
2. The controller mutates the parsed value in memory.
3. `settings_service.set(..., expected_updated_at=updated_at)` writes the
   new value and raises `VersionConflictError` if the stored `updated_at`
   has advanced since step 1.
4. On conflict, the controller re-reads and retries up to
   `_DEPT_POLICY_CAS_MAX_ATTEMPTS` (currently `3`).  Persistent contention
   surfaces as HTTP 409 `VersionConflictError` so the caller can retry with
   fresh state rather than the loop spinning forever.

Frontend callers that mutate CAS-backed settings must handle 409 explicitly
(surface a retry toast or re-fetch + re-submit).  The pattern is implemented
in `src/synthorg/api/controllers/departments.py::_mutate_dept_policies_with_retry`
and should be reused verbatim for any future JSON-blob settings that are
mutated from multiple workers.

### Repository pagination contract

The `limit: int = DEFAULT_PAGE_SIZE, offset: int = 0` keyword-only signature is
the canonical pagination shape for `list_*`/`query` methods across the
repository layer: results are bounded by default (`DEFAULT_PAGE_SIZE` and
`DEFAULT_LIST_LIMIT` are both 100; a few domains pin their own default, such as
the 5-fact `OrgFactRepository`), and every read clamps `limit` to
`MAX_LIST_LIMIT` (10,000) so a caller cannot request an unbounded scan. There is
no `limit=None` fetch-all sentinel: callers that genuinely need the whole set
drain successive pages via the `collect_all` / `collect_all_mapping` helpers in
`synthorg.core.pagination`. `offset` pushes `LIMIT`/`OFFSET` down to the
database. The shape is carried by the `IdKeyedRepository.list_items` /
`FilteredQueryRepository.query` generics (ADR-0001) and by the bespoke list
methods below:

- `TaskRepository.list_items`, `query` (the `IdKeyedRepository` +
  `FilteredQueryRepository` generics; filterable + paginated, documented in
  detail below).
- `ConnectionRepository.list_items`, `query` (the `IdKeyedRepository` +
  `FilteredQueryRepository` generics; paginated since the durable backends
  landed; in-memory stubs and durable repos share the signature).
- `WebhookReceiptRepository.get_by_connection` (already had `limit`; gained
  `offset` so callers can walk the receipt log without re-issuing).
- `SessionRepository.list_all`, `list_by_user`.
- `McpInstallationRepository.list_all` (deterministic order via
  `installed_at ASC, catalog_entry_id ASC` so pages are stable across
  identical-timestamp inserts).
- `CostRecordRepository.query` (filtered; orders by `timestamp DESC,
  agent_id ASC` before slicing).
- `OrgFactRepository.query` (existing `limit=5` default kept; `offset`
  added) and `list_by_category`.
- `OntologyEntityRepository.list_entities`, `search` (`limit` defaults to
  `DEFAULT_LIST_LIMIT` with `offset` pagination, ordered by name).
- `ApiKeyRepository.list_by_user`.

API endpoints that expose these reads wrap the page in a `PaginatedResponse`
via `synthorg.api.pagination.paginate_cursor` (see the next section); the
repository layer itself stays cursor-agnostic.

### Task pagination contract

`TaskEngine` exposes two read methods composed over the repository's canonical
`list_items` / `query` (which follow the no-`limit=None` shape above):

- `list_tasks(*, status=None, assigned_to=None, project=None, limit=None, offset=0)`
  returns a `tuple[Task, ...]` ordered by primary key `id` (ascending, stable
  across calls).  When `limit` is set it is pushed down to the repository so
  the result set is bounded.  `limit=None` is an engine-level fetch-all
  convenience -- NOT a repository sentinel (the repository layer has no
  `limit=None`; it bounds every read and callers needing the whole set drain
  pages via `collect_all`) -- drained under the in-memory `_MAX_LIST_RESULTS`
  (10,000) safety cap.  `offset > 0` requires a paired explicit `limit`
  (rejected with `ValueError` otherwise) so the returned total stays accurate.
- `count_tasks(*, status=None, assigned_to=None, project=None)` issues a
  dedicated `SELECT COUNT(*)` with the same filter semantics and is used
  when callers need an authoritative total alongside the windowed result
  set.

`TaskEngine.list_tasks(..., include_total=True)` composes both methods: the
page comes from `list_tasks` and (when `include_total=True`) the cardinality
comes from a separate `count_tasks` call.  `include_total=False` skips the
second round trip entirely, meant for callers that only need `has_more`
semantics via an extra page, not an exact total. Negative `limit` or
`offset` is rejected at the engine boundary with `ValueError`.

### Cursor-paginated list endpoints

List endpoints standardise on the `paginate_cursor` helper in
`src/synthorg/api/pagination.py` and return `PaginatedResponse[T]` with
the wire envelope documented in `web/CLAUDE.md`
(``PaginationMeta { limit, next_cursor, has_more }``).
Cursors are HMAC-signed offsets bound to a per-deployment secret
(`SYNTHORG_PAGINATION_CURSOR_SECRET`), so a tampered cursor surfaces as
HTTP 400 `InvalidCursorError` without leaking internal IDs or counts.

The contract requires every paginated endpoint to feed `paginate_cursor`
a **deterministically-sorted** sequence so cursor offsets remain stable
across calls. The sort key MUST be **total**; ties on the visible
sort fields create page-boundary drift (a request for the next page can
return a row already on the previous page, or skip a row entirely).
When the natural sort fields are not unique, append the entity's stable
identifier as a final tie-breaker. Currently paginated endpoints and
their sort keys:

| Endpoint                              | Sort key                                              |
|---------------------------------------|-------------------------------------------------------|
| `GET /api/v1/users`                   | `id` (already unique)                                 |
| `GET /api/v1/subworkflows`            | `(name, latest_version, subworkflow_id)`              |
| `GET /api/v1/integrations/health`     | connection name (unique within the catalog)           |
| `GET /api/v1/settings`                | `(definition.namespace, definition.key)` (unique pair)|

The integration-health endpoint additionally probes only the
connections on the current page (a 100-connection catalog stops paying
100 upstream probes per request).  Frontend stores reach the wire
envelope through `unwrapPaginated` (single page) or `paginateAll` (full
snapshot via cursor walk), both in `web/src/api/client.ts`.

### Backend capability methods

When an application service needs a subsystem whose construction differs
between backends (e.g. ontology versioning that wraps an
``aiosqlite.Connection`` for SQLite but an ``AsyncConnectionPool`` for
Postgres) the pattern is a ``build_*()`` method on ``PersistenceBackend``
instead of an ``isinstance(persistence, ConcreteBackend)`` branch at the
call site. Current examples include ``build_lockouts(auth_config)``,
``build_escalations(notify_channel)``, and ``build_ontology_versioning()``.
Callers always type against the protocol; the factory hides the dialect
choice. This matches the "no ``isinstance`` against concrete persistence
backends outside ``persistence/`` or its factory" rule enforced by the
code audit (ARC-1, #1491).

## Schema patterns

The schema (`src/synthorg/persistence/postgres/schema.sql` and its SQLite
sibling) mixes two write patterns:

**Mutable tables**: canonical state with in-place updates. Examples:
`users`, `settings`, `agent_states`, `heartbeats`, `approvals`,
`custom_rules`.  Rows are updated on every state transition; row count stays
bounded. Concurrent updates are serialised by MVCC + application-level CAS
(settings use `updated_at` as an etag; see `SettingsRepository.set` and
`set_many`).  Both `approvals` (human-in-the-loop approval queue,
`pending`/`approved`/`rejected`/`expired` state machine) and `custom_rules`
(operator-defined alert thresholds) exist on both backends; previously
they shipped only on SQLite and the Postgres parity gap was closed in the
budget-persistence audit.

**Append-only time-series tables**: facts with a timestamp column, never
updated in place. Examples: `cost_records`, `audit_entries`,
`lifecycle_events`, `messages`, `task_metrics`, `collaboration_metrics`,
`login_attempts`.  These tables grow linearly with system activity and are the
primary candidates for time-based partitioning.  `cost_records` and
`audit_entries` both have the partitioning column (`timestamp`) composed into
the primary key so they can be converted to TimescaleDB hypertables without
touching any application code.

**Monetary columns carry a sibling `currency TEXT NOT NULL` column.**
`cost_records`, `task_metrics`, and `agent_states` each store an ISO 4217 code
alongside the numeric cost (or `accumulated_cost`) so the unit travels with the
value. A DB-level CHECK constraint (`currency ~ '^[A-Z]{3}$'` on Postgres,
`currency GLOB '[A-Z][A-Z][A-Z]'` on SQLite) keeps direct-SQL writes honest.
Aggregators read both columns and enforce a same-currency invariant at
read time; mixing currencies in a single rollup raises
`MixedCurrencyAggregationError`.  New currency columns default to `'USD'`
(the provider-native token-pricing unit for every major LLM vendor the
project integrates with) so existing deployments migrate without a manual
backfill. Operators running a non-USD deployment can re-stamp historical
rows after the migration runs by executing a targeted
`UPDATE <table> SET currency = '<ISO 4217 code>' WHERE currency = 'USD'`
statement; SynthOrg does not provide a post-migrate hook for this because
the correct target currency depends on the operator's business model.

**Currency is a display preference, not a conversion unit.**  SynthOrg
stamps each record with the operator's configured `budget.currency` but
does not perform FX conversion on the numeric values. Every major LLM
provider (Anthropic, OpenAI, Google Gemini, Mistral, Cohere, Groq, et al.)
publishes token pricing in USD, and LiteLLM returns `response_cost` in USD
too. Changing `budget.currency` relabels the display symbol for future
rows; it does not translate the numbers. Cross-currency reporting (with a
real FX rate source and timestamped conversion) is a separate feature
tracked on the roadmap.

`heartbeats` is deliberately **excluded** from the append-only set. Despite
having a timestamp, it stores one row per `execution_id` and updates that row
on every pulse; the write pattern is update-heavy and the row count is
bounded by the number of live executions. Hypertables optimise for immutable
append-only data, so converting `heartbeats` would be the wrong choice.

## Time-series tables and TimescaleDB hypertables

For append-only tables on Postgres deployments, SynthOrg supports converting
`cost_records` and `audit_entries` into TimescaleDB hypertables. Hypertables
transparently partition the data into time-bucketed chunks so queries that
filter on `timestamp` scan a bounded subset of chunks rather than the whole
table, and operations like `DROP TABLE` or chunk eviction become O(chunk
count) rather than O(row count).

The feature is **off by default** and gated behind
`PostgresConfig.enable_timescaledb`.  Operators running vanilla Postgres or a
managed service without TimescaleDB leave it off and the tables stay regular
relational tables with a composite primary key. Note: the composite-primary-key
schema change and its yoyo revision run unconditionally (they are valid on
vanilla Postgres); only the `create_hypertable` step is gated behind the flag.

```python
PostgresConfig(
    ...,
    enable_timescaledb=True,
    cost_records_chunk_interval="1 day",
    audit_entries_chunk_interval="1 day",
)
```

When enabled, the backend's `migrate` method runs two phases: first yoyo
applies the schema revisions, then a dedicated step calls
`create_hypertable` on each target table. The conversion is idempotent
(`if_not_exists => TRUE`) so reruns and restores are safe. If the
`timescaledb` extension is not installed on the server, the flag is treated as
a best-effort hint and the backend logs a warning rather than failing the
migration; this lets operators leave the flag true in shared config and have
it degrade gracefully on clusters that do not support it.

**Scope: Apache-2.0 features only.**  The Postgres backend uses exclusively
TimescaleDB features that ship under Apache-2.0: core hypertables,
`create_hypertable`, chunk management, and `drop_chunks`.  Retention policies,
compression, and continuous aggregates are under the Timescale License and are
**not** used; they would force every deployment to accept the Timescale
License terms and would not run on the OSS image (`-oss` tag).  Self-hosted
operators who want retention today can call `drop_chunks('cost_records',
older_than => INTERVAL '90 days')` on their own cron schedule until SynthOrg
grows a backend-owned retention policy that stays within Apache-2.0.

| Table           | Included | Rationale                                                         |
|-----------------|----------|-------------------------------------------------------------------|
| `cost_records`  | Yes      | LLM call costs; append-only; highest-volume time-series table.    |
| `audit_entries` | Yes      | Security events; append-only; compliance queries are time-bound.  |
| `heartbeats`    | No       | Update-heavy (per-execution row bump); hypertable semantics wrong. |

### Managed-service compatibility

TimescaleDB is a self-hosted-only feature. The major managed Postgres offerings
(AWS RDS, Google Cloud SQL) do not allow custom extensions; operators
cannot install `timescaledb` there. Azure Database for PostgreSQL
Flexible Server is an exception: it supports TimescaleDB as an extension. SynthOrg runs
cleanly on all of them; leave `enable_timescaledb=False` and the schema stays
fully relational. The composite primary keys on `cost_records` and
`audit_entries` are valid on vanilla Postgres and do not require TimescaleDB to
function; they just preserve the option of turning hypertables on later if the
deployment moves to self-hosted.

| Deployment target            | TimescaleDB support | Recommended setting |
|------------------------------|---------------------|---------------------|
| Self-hosted Postgres 18+     | Operator-installed  | `enable_timescaledb=True` |
| AWS RDS / Aurora Postgres    | Not available       | `enable_timescaledb=False` |
| Google Cloud SQL Postgres    | Not available       | `enable_timescaledb=False` |
| Azure Database for Postgres (Flexible Server) | Supported (extension) | `enable_timescaledb=True` |
| Docker / local dev           | `timescale/timescaledb:latest-pg18-oss` | `enable_timescaledb=True` |

## Extension strategy

Postgres extensions need two things to work through the migration
pipeline: the extension DDL has to apply cleanly through yoyo against
the production database, and any catalog objects the extension
creates after migrations run must not be flagged as drift by the
declared-vs-revisions gate. SynthOrg handles this with a two-step
pattern:

1. **Declared schema** (`schema.sql` + revision files) only contains
   DDL that is valid on vanilla Postgres. Function-call SQL like
   `SELECT create_hypertable(...)` cannot live here because the
   drift gate compares structural shape and side-effects of those
   function calls would never appear in `pg_dump` output.
2. **Runtime setup hooks** in the backend's `migrate` method run
   post-revision SQL against the real target database. These hooks
   detect extension availability via `pg_available_extensions` and
   skip gracefully when the extension is not installed, so the same
   config works on vanilla Postgres and on self-hosted TimescaleDB
   without branching at deployment time.

This pattern scales to other extensions (`pgvector`, `pg_trgm`,
`pgcrypto`) if SynthOrg adopts them later. The rule is: if the
extension adds objects that the drift gate cannot recognise, add a
runtime setup hook; if the extension is purely about
`CREATE EXTENSION` and then standard DDL, let the revisions own it.

## Migration workflow

Schema migrations are applied via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/)
against the revisions under `src/synthorg/persistence/<backend>/revisions/`.
The single source of truth for the declared schema lives in
`src/synthorg/persistence/<backend>/schema.sql`.  The full
developer-facing workflow, happy path, and AI-agent rules live in
[`docs/guides/persistence-migrations.md`](../guides/persistence-migrations.md);
read it before adding a new revision. The short form:

```text
src/synthorg/persistence/sqlite/revisions/<14-digit-ts>_<name>.sql
src/synthorg/persistence/postgres/revisions/<14-digit-ts>_<name>.sql
```

Never hand-edit a revision file that already exists on `origin/main`;
yoyo's content-hash check refuses to re-apply an edited file (the
hash mismatch trips at apply time).  If a revision needs to change
before it has landed (still PR-local), delete the file and author a
new one with the corrected SQL.

Procedural setup that cannot be expressed as a static `CREATE` /
`ALTER` statement (function-call DDL like `SELECT
create_hypertable(...)`) does NOT belong in the `revisions/`
directory; it runs through the backend's runtime migration hooks
(see the TimescaleDB pattern above).  The drift gate is structural
and would not understand the side-effect anyway.

## Migration squashing

As the revision count grows the directory becomes harder to review.
SynthOrg supports a single-baseline squash that collapses the entire
revision chain into one seed file derived from `schema.sql`.  Run it
rarely; the revision history is the audit trail between squashes.

### Procedure

1. Verify clean state: `scripts/check_schema_drift_revisions.py
   --backend <backend>` exits 0.
2. Delete every revision file except `__init__.py`.
3. Copy `schema.sql` verbatim to
   `revisions/00000000000000_baseline.sql` (the leading 14 zeros
   ensure it sorts before any future revision).
4. Re-run the drift gate to confirm the new seed reproduces
   `schema.sql` exactly.

Repeat for the other backend.

### Upgrade paths after squash

Pre-alpha: there are no production databases that preserve the prior
revision chain, so the squash does not need to support upgrade paths
from a pre-squash state. Operators run a fresh install or restore
from a backup. Once SynthOrg has external users, the squash
procedure will need an explicit "fresh-install marker" workflow via
`migrations.migrate_baseline()`.

### Committing a squash

Squash commits delete old revision files, which the pre-commit hook
`check_no_modify_migration.sh` would normally block. Set the
`SYNTHORG_MIGRATION_SQUASH` environment variable to bypass:

```bash
SYNTHORG_MIGRATION_SQUASH=1 git commit -m "refactor(persistence): squash revisions"
```
