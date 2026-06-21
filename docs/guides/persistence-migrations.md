# Persistence Migrations

Opinionated guide for touching the persistence layer. Read before
editing anything under `src/synthorg/persistence/`.

## The Rule

`src/synthorg/persistence/` is the **only** place in SynthOrg that
may import `aiosqlite`, `sqlite3`, `psycopg`, or `psycopg_pool`, or
emit raw SQL DDL/DML keywords in string literals.

A small set of files are sanctioned exceptions. The authoritative list is the
`_ALLOWLIST` frozenset in `scripts/check_persistence_boundary.py`; the two primary
agent-facing entries are:

- `src/synthorg/tools/database/schema_inspect.py`: agent-facing
  introspection tool; returns arbitrary DB metadata the repository
  abstraction does not expose.
- `src/synthorg/tools/database/sql_query.py`: agent-facing
  arbitrary-SQL tool; the SQL string itself is the payload, so it
  cannot ride the repository pattern.

Enforced by `scripts/check_persistence_boundary.py` in pre-push and
CI Lint. Opt-out on a single line with a trailing
`# lint-allow: persistence-boundary -- <reason>` comment (the
justification after `--` is required); permanent exceptions belong in
`_ALLOWLIST`.

## Tooling

Schema migrations run through `synthorg.persistence.migrations`, a
thin async wrapper around [yoyo-migrations](https://ollycope.com/software/yoyo/latest/).
Yoyo is in-process Python (no external binary), uses the
`postgresql+psycopg://` URL scheme to reuse the project's psycopg 3
dependency, applies each revision in a single transaction by default,
and tracks applied revisions plus their content hash in the
`_yoyo_migration` table. An audit trail of every apply / rollback /
mark lands in `_yoyo_log` automatically.

## Happy Path: Adding a New Durable Table

1. **Define the repository Protocol** in
   `src/synthorg/persistence/<domain>_protocol.py`.  Inherit from
   `typing.Protocol`, decorate with `@runtime_checkable`, and keep
   the method surface thin: CRUD only. Composite orchestration
   (search, manifest assembly) belongs in a service layer, not the
   repository.

2. **Implement both backends.**
   - `src/synthorg/persistence/sqlite/<entity>_repo.py`:
     `SQLite<Name>Repository(db: aiosqlite.Connection)`.
   - `src/synthorg/persistence/postgres/<entity>_repo.py`:
     `Postgres<Name>Repository(pool: AsyncConnectionPool)`.

   Use `# noqa: TC001` on runtime-needed type imports; Pydantic v2
   evaluates annotations at runtime.

3. **Edit BOTH schema files in lockstep.**
   - `src/synthorg/persistence/sqlite/schema.sql`
   - `src/synthorg/persistence/postgres/schema.sql`

   Keep types Postgres-native (JSONB, TIMESTAMPTZ, BIGINT, BOOLEAN)
   on the Postgres side and SQLite-native (TEXT, INTEGER, REAL) on
   the SQLite side. Monetary columns carry a sibling
   `currency TEXT NOT NULL` with a CHECK constraint.

4. **Author one revision file per backend.**

   Filename convention: `<14-digit-timestamp>_<short_name>.sql` (e.g.
   `20260512083000_add_widgets_table.sql`).  The 14-digit prefix is
   what the single-revision-per-PR gate relies on for ordering.

   ```text
   src/synthorg/persistence/sqlite/revisions/20260512083000_add_widgets.sql
   src/synthorg/persistence/postgres/revisions/20260512083000_add_widgets.sql
   ```

   The revision SQL must reproduce the change you made to
   `schema.sql`.  Drift between the two trips the CI gate.

5. **Expose on `PersistenceBackend`** in
   `src/synthorg/persistence/protocol.py`.  Add a `@property`.  Wire
   instantiation into both `SQLitePersistenceBackend._create_repositories()`
   and `PostgresPersistenceBackend._create_repositories()`.

6. **Write conformance tests** under
   `tests/conformance/persistence/test_<entity>_repository.py`.  Use
   the existing `backend` fixture in `conftest.py`; it parametrizes
   over `["sqlite", "postgres"]` and applies migrations fresh per
   test. Postgres arm auto-skips when Docker is unavailable.

## Validating schema parity

Two complementary gates run in pre-push and CI:

- `scripts/check_schema_drift.py` (cross-backend): compares the two
  `schema.sql` files via sqlglot and the two `revisions/`
  directories by filename suffix. Catches per-column type drift,
  NOT NULL / PRIMARY KEY / UNIQUE constraint drift, one-sided
  indexes, and shared-name index attribute mismatches (UNIQUE /
  WHERE / USING).  Cross-backend drift is recorded in
  `scripts/schema_drift_baseline.txt` with a per-entry justification.

- `scripts/check_schema_drift_revisions.py` (declared vs applied):
  applies the declared `schema.sql` and the accumulated
  `revisions/*.sql` to two fresh throwaway databases via yoyo, dumps
  each schema (`sqlite_master` for SQLite, `pg_dump --schema-only`
  for Postgres), and structurally diffs the two dumps. Run per
  backend:

  ```bash
  uv run python scripts/check_schema_drift_revisions.py --backend sqlite
  uv run python scripts/check_schema_drift_revisions.py --backend postgres   # requires Docker
  ```

  Zero drift is the only acceptable state; the gate has no baseline
  escape hatch.

## What You Must Not Do

- **Never** import `aiosqlite` / `sqlite3` / `psycopg` /
  `psycopg_pool` outside `src/synthorg/persistence/`.
- **Never** hand-edit a revision file that already exists on
  `origin/main`.  yoyo's content-hash check refuses to re-apply an
  edited file, breaking every database that already ran it. Author
  a new revision with your delta instead. Enforced by
  `scripts/check_no_modify_migration.sh`.
- **Never** land more than one new revision file per PR per backend
  (so two total at most).  Enforced by
  `scripts/check_single_migration_per_pr.sh`.  If you find yourself
  wanting two, consolidate them: delete the in-progress files and
  author a single new one.
- **Never** reach for `persistence._db` or `persistence._pool` via
  `getattr` to smuggle driver primitives across the boundary.
  Expose a method on the backend instead (see `build_escalations`,
  `build_lockouts` for the pattern).

## For AI Agents

If your change touches persistence and you feel an urge to
`import aiosqlite` outside `src/synthorg/persistence/`, stop. That
is the symptom the boundary is designed to catch. Instead:

1. Ask whether this operation belongs in a repository. Almost
   always, yes.
2. If the operation is genuinely outside the repository contract
   (an agent tool, a migration-time script, a diagnostic probe),
   propose adding the path to `_ALLOWLIST` in
   `scripts/check_persistence_boundary.py` with a justifying
   comment, and surface the proposal to the user before committing.
3. For one-off lines (a test fixture, a legacy caller being
   incrementally migrated), use the inline marker:

   ```python
   import aiosqlite  # lint-allow: persistence-boundary -- fixture bootstrap
   ```

   The justification text after `--` is non-empty by design so the
   opt-out is auditable.

## Squash Procedure

A full single-baseline squash collapses every historical revision
into one `00000000000000_baseline.sql` per backend, derived from the
current `schema.sql`.  Run this rarely; the revision history is the
audit trail between squashes.

Per backend (sqlite first, then postgres):

```bash
# 1. Verify clean state before squashing.
uv run python scripts/check_schema_drift_revisions.py --backend sqlite

# 2. Remove every revision file except __init__.py.
rm src/synthorg/persistence/sqlite/revisions/*.sql

# 3. Write schema.sql as the new seed (preserve sort-first naming).
cp src/synthorg/persistence/sqlite/schema.sql \
   src/synthorg/persistence/sqlite/revisions/00000000000000_baseline.sql

# 4. Verify the new seed reproduces schema.sql exactly.
uv run python scripts/check_schema_drift_revisions.py --backend sqlite
```

Repeat for `postgres`.  Commit with the bypass env var so the
single-revision-per-PR hook accepts the squash:

```bash
SYNTHORG_MIGRATION_SQUASH=1 git commit -m "refactor(persistence): squash revisions"
```

After squashing, `00000000000000_baseline.sql` is the only file in the
revisions directories that exists on `origin/main`, so
`check-no-modify-migration` blocks any in-branch edit to it under the
standard immutability rule (the hook has no per-file special-casing).

The baseline is a fresh-install seed: it reproduces the final schema,
not the historical sequence. Any data-migration DML the collapsed
revisions carried (one-off data fixes, SQLite table-rebuild
`INSERT ... SELECT` copies) is intentionally absent -- a fresh database
never needs it.
SynthOrg is pre-alpha with no deployed databases, so only the
fresh-install path matters; see `docs/design/persistence.md` for the
upgrade-path stance.

## Troubleshooting

**Drift gate reports a finding after editing schema.sql**
→ Author a matching revision file under
`src/synthorg/persistence/<backend>/revisions/`.  The revision's SQL
must produce the same shape as schema.sql when applied to a fresh
database.

**`check-no-modify-migration` fires on a revision your branch added**
→ The hook compares against `origin/main`; a file your branch added
is not on origin yet, so the check passes. If it fires anyway you
have probably edited a file that already exists on `main`; restore
it with `git restore --source=origin/main -- <path>` and author a
new revision file with your delta instead.

**`pg_dump` complains about missing extensions when running the
Postgres drift gate**
→ The testcontainer image must match the production Postgres
version. The default is `postgres:18-alpine`; override at the call
site if your environment needs a different tag.

**Pre-push `persistence-boundary` hook fires**
→ Legitimate agent tool: add the path to `_ALLOWLIST` in
`scripts/check_persistence_boundary.py` with a justifying comment
and surface the diff to a reviewer. One-off test fixture: prefer
the inline `# lint-allow: persistence-boundary -- <reason>` marker
(per-line auditable) over growing the allowlist silently.

---

Referenced from `CLAUDE.md`, `docs/design/persistence.md`, and the
header comment of every revision file added post-squash.
