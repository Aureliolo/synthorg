# Persistence Boundary: Exception Categories

On-demand reference. The top rule in `CLAUDE.md` is: `src/synthorg/persistence/` is the **only** place that may import `aiosqlite`, `sqlite3`, `psycopg`, or `psycopg_pool`, or emit raw SQL DDL/DML keywords in string literals. Every durable feature must define a repository Protocol under `persistence/<domain>_protocol.py`, concrete impls under `persistence/{sqlite,postgres}/`, and expose them on `PersistenceBackend`.

## Three sanctioned exception categories

Sanctioned exceptions cover three categories. The authoritative list lives in `_ALLOWLIST` inside `scripts/check_persistence_boundary.py`; any new exception must be added there with a justifying comment.

### 1. Agent-facing DB tools

- `src/synthorg/tools/database/schema_inspect.py`
- `src/synthorg/tools/database/sql_query.py`

### 2. Security / scanning utilities that inspect user-supplied SQL

- e.g. `src/synthorg/security/rules/destructive_op_detector.py`, whose detection payload *is* DDL keyword strings.

### 3. Test fixtures / conformance harnesses

- Hold driver primitives for cross-subsystem setup.

## Shared helpers

`src/synthorg/persistence/_shared/` is the canonical home for backend-agnostic serialisation, deserialisation, error-classification, and timestamp-normalisation logic. Repositories pass driver-specific bits (JSON wrappers, error-class predicates) in as callables so the helpers stay portable. Current helpers:

- `datetime_marshaller.py`: strict pair `parse_iso_utc(str) -> datetime` and `format_iso_utc(datetime) -> str`. Both reject naive datetimes (`ValueError`) and normalise to UTC. Use these for any persistence path that round-trips ISO 8601 timestamps through TEXT columns, JSON envelopes, or settings DTOs.
- `coerce_row_timestamp(value: str | datetime) -> datetime`: canonical row-deserialisation dispatcher. Every repository `_row_to_*` helper should call it; it tolerates SQLite TEXT (`str`), SQLite TEXT with `detect_types=PARSE_DECLTYPES` (`datetime`), Postgres `TIMESTAMPTZ` (tz-aware `datetime`, possibly in the session timezone), and legacy / migrated rows persisted as ISO strings even where the column is now typed.
  - **String path**: routed through `parse_iso_utc` (strict naive rejection; a naive ISO string surfaces as `ValueError`).
  - **Datetime path**: routed through `normalize_utc` (treats naive as UTC and calls `astimezone(UTC)` on aware values).
  - **Error path**: any other input type raises `TypeError` so a corrupt row surfaces loudly via the enclosing `MalformedRowError` / `QueryError` handler instead of silently producing garbage.
- `normalize_utc(datetime) -> datetime`: relaxed coercer (treats naive as UTC, calls `astimezone(UTC)` on aware). Used internally by `coerce_row_timestamp`'s datetime branch. Call directly only when the input is statically known to be a `datetime` (e.g. when the caller has already produced a `datetime.now(UTC)` and just needs to defend against a future code change introducing a non-UTC offset).
- `audit.py`: shared `AuditEntry` row<->payload helpers (`audit_entry_to_payload`, `row_to_audit_entry`, `classify_audit_save_error`).
- `custom_rule.py`: shared custom-rule deserialisation (`row_to_custom_rule`, `serialize_altitudes`).
- `rows.py`: the `RowLike` `@runtime_checkable` protocol (a string-key `__getitem__`). Both `aiosqlite.Row` and psycopg `dict_row` mappings satisfy it, so a single `RowLike`-typed marshalling module serves both backends without importing a driver.
- `pagination.py`: shared pagination-argument validation (`validate_pagination_args`).
- Per-entity row<->model marshalling modules, one per aggregate, each shared by the SQLite and Postgres repositories: `charter_marshalling.py`, `cost_forecast_marshalling.py`, `org_fact_marshalling.py`, `workflow_definition_marshalling.py`, `workflow_execution_marshalling.py`. Each exposes a `row_to_*` deserialisation function (consuming `RowLike`, normalising the JSONB-vs-TEXT and `TIMESTAMPTZ`-vs-ISO divergence); most also expose the column list and a `build_*_where` clause builder taking the backend's placeholder token (`org_fact_marshalling.py` exposes only the deserialisers, as its MVCC SQL stays in the backend modules). JSON wrapping on the write path (`json.dumps` vs psycopg `Jsonb`) stays in the backend repos so these modules never import a driver.

When to use which: the strict pair (`parse_iso_utc` / `format_iso_utc`) sits at the boundary where ISO strings cross the persistence layer (settings DTOs, JSON envelopes, SQLite TEXT writes); `coerce_row_timestamp` sits inside `_row_to_*` deserializers where the driver shape is uncertain; `normalize_utc` is the lowest-level primitive and is rarely called directly by repository code.

Each shared helper carries a dedicated unit suite under `tests/unit/persistence/_shared/` (e.g. `test_datetime_marshaller.py`, `test_rows.py`, `test_<entity>_marshalling.py`) and is exercised end-to-end by every backend conformance test that round-trips the relevant entity (`test_audit_repository.py`, `test_custom_rule_repo.py`, `test_settings_repo.py`, `test_charter_repository.py`, etc.).

Adding a new shared helper: extract the duplicated logic into `_shared/`, add a dedicated unit suite alongside it (`test_<helper>.py`), and add a conformance test that runs against both backends.

## Repository-private row marshalling helpers

When a repository's row<->model mapping is not (yet) shared across backends, the per-repository helpers follow a fixed naming pair so the deserialise and serialise directions are unambiguous:

- `_row_to_<noun>(row: RowLike) -> Model`: **deserialise** a single driver row into its domain model. The `<noun>` is the aggregate, not the table (`_row_to_run`, `_row_to_checkpoint`, `_row_to_author`), so a module that reconstructs several models reads cleanly. Each helper routes every timestamp column through `coerce_row_timestamp` and re-raises driver/parse failures as the layer's `MalformedRowError`. The `from_row` / `<noun>_from_row` ordering is **not** used; the verb-then-noun `_row_to_<noun>` form is canonical.
- `_to_row(self, entity) -> dict[str, object]` (or an explicit column tuple): **serialise** a domain model into the driver's parameter shape. JSON wrapping that diverges by driver (`json.dumps` on SQLite, psycopg `Jsonb` on Postgres) stays on the backend side of this boundary; the helper produces only plain Python values.

These mirror the shared `row_to_*` functions in `_shared/` (same direction, same timestamp discipline); the leading underscore marks the ones that are still repository-private because no second backend consumes them yet. Promoting a private `_row_to_<noun>` to a shared `row_to_<noun>` is the standard move when the SQLite and Postgres repos start to duplicate it.

## Rollback discipline

Repositories that open a write transaction expose a private coroutine for the failure path. The recommended form for new code is:

```python
async def _safe_rollback(self, *, event: str) -> None:
```

It rolls back the active transaction and swallows-then-logs any rollback-time driver error (a failed rollback must never mask the original write failure being handled), logging under the supplied `event` constant with `error_type` + `safe_error_description`. Call it from the `except` arm of a write before re-raising the domain error; never inline a bare `await conn.rollback()` (which would let a rollback-time exception escape and shadow the real cause).

Existing repositories carry several historical shapes (instance vs module-level helper, `event` vs `operation`/`failure_event` parameter name, positional vs keyword-only, some taking the connection explicitly); these predate the recommended form and are not yet normalised. New repositories should follow the keyword-only `event` signature above.

## In-memory invariant pins (interim, schema-deferred)

When a Pydantic model gains a required field but the corresponding column hasn't been added yet (e.g. a yoyo revision is queued in a follow-up issue), the repository may carry a process-local `_pinned_<field>` map keyed by the row's primary key, plus a true **per-key lock registry** (not a fixed-size stripe set): `_lock_registry: dict[str, asyncio.Lock]` lazily populated under a small `_registry_lock` so each primary key gets its own dedicated `asyncio.Lock`. Concurrent operations on different keys never block each other; the per-key lock is held across the full critical section -- check-and-set + DB I/O + deserialise -- so concurrent first-writes for the *same* key cannot diverge the in-memory dict from the durable row. Mismatched-pin writes raise the same domain error a column constraint would (e.g. `MixedCurrencyAggregationError`). On any failure mode (DB error, missing RETURNING row, deserialise failure) a `try`/`finally` around the I/O block rolls the pin back so a retry isn't blocked by a phantom pin. The read path (`get`) uses a bare `dict.get` -- atomic under the GIL, never yields -- and falls back to a sane neutral default (`DEFAULT_CURRENCY` for currency, etc.) when no pin is present, with a DEBUG log per pin-miss. The schema-gap notice is emitted at INFO **once per process** via a module-level guard, not per repo instance, so test suites that build many repositories don't flood the log.

Canonical example: `ProjectCostAggregateRepository` in `persistence/{sqlite,postgres}/project_cost_aggregate_repo.py`. The `currency: CurrencyCode` field is required on `ProjectCostAggregate` but the durable column is queued under #1597; both repos hold `_pinned_currencies: dict[str, str]` plus the per-key `_lock_registry: dict[str, asyncio.Lock]` (guarded by `_registry_lock` for lazy init) and emit `PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING` at INFO once per process.

This pattern is interim by construction. Each pin must reference an issue tracking the schema follow-up; once the column lands, the pin and its DEBUG/INFO logging come out in the same change.

## In-memory fallbacks

In-memory fallbacks in `persistence/integration_stubs.py` are named `InMemoryXRepository` (NOT `StubXRepository`) to signal that they are *working* repositories, just process-local and non-durable. The connection-family backends (Connection, ConnectionSecret, OAuthState, WebhookReceipt) now ship durable SQLite + Postgres implementations alongside these fakes; the `InMemory*` classes remain only for unit-test fakes that don't want to spin up a real database.

## Service layer

Controllers and API endpoints access persistence through domain-scoped **service layers** (e.g. `ArtifactService`, `WorkflowService`, `MemoryService`, `CustomRulesService`, `UserService`, `ProjectService`, `SsrfViolationService`, `SettingsService`) rather than reaching into repositories directly.

Services:

- Keep controllers thin (parse / shape / return).
- Centralise `API_*` / `META_*` / `WORKFLOW_DEF_*` audit logging in one place.
- Own cross-repo orchestration (e.g. workflow-definition delete cascading to version snapshots).

Repositories **must not** log mutations themselves (enforced by `scripts/check_persistence_boundary.py`). The service layer is the canonical logging point so audit trails do not duplicate when multiple callers share a repo. Repos may still log fetch telemetry (`*_FETCHED`, `*_LISTED`, `*_COUNTED`) and error paths (`*_SAVE_FAILED`, `*_DELETE_FAILED`, `*_DUPLICATE`); the rule targets entity-mutation audit specifically.

## Cross-backend write_context

`PersistenceBackend.write_context()` returns an async context manager that callers wrap around mutating SQL. The call shape is identical on both backends:

```python
async with backend.write_context():
    await artifact_repo.save(artifact)
    await project_repo.save(project)
```

The mutual-exclusion guarantee, however, is backend-specific:

- **SQLite** acquires a shared in-process `asyncio.Lock`. The single `aiosqlite.Connection` is shared by every repository on that backend, so concurrent writers must serialise at the statement level. Repositories receive the backend's `write_context` bound method at construction and call `async with self._write_context():` around every multi-statement transaction.
- **Postgres** yields immediately. Each repository operation checks out an independent connection from the async pool, so writers are isolated at the database level without an in-process lock. The method exists only to keep the cross-backend interface uniform; it does not provide mutual exclusion beyond what the pool already gives.

Callers must not rely on `write_context()` for distributed mutual exclusion or cross-backend serializability; for true cross-process locking, use a database-side primitive.

Repositories never own a private write lock. Tests that construct a repository in isolation use `tests._shared.persistence.make_private_write_context()` to satisfy the constructor; that helper returns a fresh per-call lock and is unsafe for production wiring.

## Migrations

Adding a migration: read `docs/guides/persistence-migrations.md` first. Never hand-edit a revision file that already exists on `origin/main`; yoyo's content-hash check refuses to re-apply an edited file. Author a new revision with your delta instead.

## Per-line opt-out

`# lint-allow: persistence-boundary -- <required justification>` as a trailing comment. The `--` separator is part of the opt-out syntax itself; the justification after it must be non-empty.

## Enforcement

`scripts/check_persistence_boundary.py` (pre-push hook + CI Lint job).

`scripts/check_no_api_dto_in_persistence_or_service.py` (pre-push hook + CI Lint job) is the sibling layer-discipline gate added in WP-1. It forbids `from synthorg.api.dto_*` imports inside `src/synthorg/persistence/` and `src/synthorg/service/`, so durable storage code cannot bind to API response shapes. The provider-audit and preset-override repositories switched to `synthorg.providers.management.capability_dtos` as part of the same change.

`scripts/check_dual_backend_test_parity.py` (pre-push hook + CI Lint job) protects the conformance-test arm of the same boundary: every test under `tests/conformance/persistence/` must consume the parametrised `backend: PersistenceBackend` fixture (no direct `aiosqlite` / `psycopg` typing, no `backend.backend_name == "..."` body conditionals), and every repository protocol exposed on `PersistenceBackend` must be exercised by at least one test.
