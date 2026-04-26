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

`src/synthorg/persistence/_shared/` is the canonical home for backend-agnostic serialization, deserialization, error-classification, and timestamp-normalization logic. Repositories pass driver-specific bits (JSON wrappers, error-class predicates) in as callables so the helpers stay portable. Current helpers:

- `datetime_marshaller.py`: strict pair `parse_iso_utc(str) -> datetime` and `format_iso_utc(datetime) -> str`. Both reject naive datetimes (`ValueError`) and normalize to UTC. Use these for any persistence path that round-trips ISO 8601 timestamps through TEXT columns, JSON envelopes, or settings DTOs.
- `coerce_row_timestamp(value: str | datetime) -> datetime`: canonical row-deserialisation dispatcher. Strings flow through `parse_iso_utc` (strict naive rejection); `datetime` values flow through `normalize_utc` (treats naive as UTC, calls `astimezone(UTC)` on aware); any other type raises `TypeError` so a corrupt row surfaces loudly via the enclosing `MalformedRowError` / `QueryError` path. This is the helper every repository `_row_to_*` deserialiser should call -- it tolerates SQLite TEXT (`str`), SQLite TEXT with `detect_types=PARSE_DECLTYPES` (`datetime`), Postgres `TIMESTAMPTZ` (tz-aware `datetime`, possibly in the session timezone), and legacy / migrated rows persisted as ISO strings even where the column is now typed.
- `normalize_utc(datetime) -> datetime`: relaxed coercer (treats naive as UTC, calls `astimezone(UTC)` on aware). Used internally by `coerce_row_timestamp`'s datetime branch. Call directly only when the input is statically known to be a `datetime` (e.g. when the caller has already produced a `datetime.now(UTC)` and just needs to defend against a future code change introducing a non-UTC offset).
- `audit.py`: shared `AuditEntry` row<->payload helpers (`audit_entry_to_payload`, `row_to_audit_entry`, `classify_audit_save_error`).
- `custom_rule.py`: shared custom-rule deserialization (`row_to_custom_rule`, `serialize_altitudes`).

When to use which: the strict pair (`parse_iso_utc` / `format_iso_utc`) sits at the boundary where ISO strings cross the persistence layer (settings DTOs, JSON envelopes, SQLite TEXT writes); `coerce_row_timestamp` sits inside `_row_to_*` deserialisers where the driver shape is uncertain; `normalize_utc` is the lowest-level primitive and is rarely called directly by repository code.

The new helper is covered by `tests/unit/persistence/_shared/test_datetime_marshaller.py` (unit suite, dedicated `TestCoerceRowTimestamp` class) and exercised end-to-end by every backend conformance test that round-trips a timestamped entity (`test_audit_repository.py`, `test_custom_rule_repo.py`, `test_settings_repo.py`, etc.).

Adding a new shared helper: extract the duplicated logic into `_shared/`, add a `test_*_helpers.py` unit suite alongside it, and add a conformance test that runs against both backends.

## In-memory fallbacks

In-memory fallbacks in `persistence/integration_stubs.py` are named `InMemoryXRepository` (NOT `StubXRepository`) to signal that they are *working* repositories, just process-local and non-durable. These still require durable SQLite + Postgres counterparts (tracked in issue #1517); the `InMemory*` naming does not relax that obligation.

## Service layer

Controllers and API endpoints access persistence through domain-scoped **service layers** (e.g. `ArtifactService`, `WorkflowService`, `MemoryService`, `CustomRulesService`, `UserService`, `ProjectService`, `SsrfViolationService`, `SettingsService`) rather than reaching into repositories directly.

Services:

- Keep controllers thin (parse / shape / return).
- Centralize `API_*` / `META_*` / `WORKFLOW_DEF_*` audit logging in one place.
- Own cross-repo orchestration (e.g. workflow-definition delete cascading to version snapshots).

Repositories **must not** log mutations themselves (enforced by `scripts/check_persistence_boundary.py`). The service layer is the canonical logging point so audit trails do not duplicate when multiple callers share a repo. Repos may still log fetch telemetry (`*_FETCHED`, `*_LISTED`, `*_COUNTED`) and error paths (`*_SAVE_FAILED`, `*_DELETE_FAILED`, `*_DUPLICATE`); the rule targets entity-mutation audit specifically.

## Migrations

Adding a migration: read `docs/guides/persistence-migrations.md` first. Do not hand-edit SQL in `persistence/{sqlite,postgres}/revisions/`, and do not edit `atlas.sum`. Rehashing via `atlas migrate hash` post-release is blocked by a PreToolUse hook; delete the in-progress migration and regenerate with `atlas migrate diff` instead.

## Per-line opt-out

`# lint-allow: persistence-boundary -- <required justification>` as a trailing comment. The `--` separator is part of the opt-out syntax itself; the justification after it must be non-empty.

## Enforcement

`scripts/check_persistence_boundary.py` (pre-push hook + CI Lint job).
