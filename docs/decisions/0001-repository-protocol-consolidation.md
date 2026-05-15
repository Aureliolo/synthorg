# ADR-0001: Repository protocol consolidation

## Status

Accepted, implemented in WP-1 (issue #1916).

## Context

The persistence layer ships ~44 entity-specific `*Repository` protocols
under `src/synthorg/persistence/**/*_protocol.py`. Each class has its
own bespoke method signature set, even though almost every protocol
follows one of a handful of recurring patterns:

* `Task`, `Project`, `User`, `Artifact`, ... use the same five-method
  CRUD surface: `save`, `get`, `delete`, plus pagination and filtered
  enumeration.
* `Message`, `CostRecord`, `Audit`, `Checkpoint`, `ProviderAudit` are
  append-only event logs with query + retention purge.
* `Approval`, `FineTuneRun`, `Project.create_if_absent`,
  `WorkflowDefinition.update_if_exists` are compare-and-set
  state-machine transitions in disguise.
* `Settings`, `CircuitBreakerState` use composite keys.
* `OrgFact` runs full MVCC with point-in-time snapshots.

Costs of the bespoke-per-entity approach:

1. **Drift**. Every new entity duplicates the canonical CRUD signatures
   imperfectly: `list_tasks` vs `list_users`, `get_history` vs `query`,
   `save_many` vs `bulk_insert`. Each deviation is a paper-cut for
   readers.
2. **Conformance test duplication**. The shared `backend` fixture in
   `tests/conformance/persistence/conftest.py` runs every test against
   both backends, but the tests themselves repeat the same CRUD body
   per entity.
3. **Callsite opacity**. A reader cannot tell from a callsite
   `await backend.foo.list_things(...)` whether `things` are paginated,
   filterable, or just dumped. Uniform method names (`list_items`,
   `query(filter_spec)`) make the pattern explicit.
4. **Mypy strict friction**. Each bespoke protocol gives mypy a
   different surface to check; a generic surface centralises the
   strictness work.

## Decision

Replace the bespoke per-entity protocols with six generic categories
defined in `src/synthorg/persistence/_generics.py`. Concrete protocols
compose multiple generics via Protocol inheritance.

### The six categories

| Category | Type vars | Methods |
|---|---|---|
| `SingletonRepository[T]` | `T` | `get`, `upsert`, `delete` (no id arg) |
| `IdKeyedRepository[T, ID]` | `T`, `ID` | `save`, `get(entity_id)`, `delete(entity_id)`, `list_items(*, limit, offset)` |
| `FilteredQueryRepository[T, FilterSpec]` | `T`, `FilterSpec` | `query(filter_spec, *, limit, offset)`, `count(filter_spec)` |
| `AppendOnlyRepository[Event, FilterSpec]` | `Event`, `FilterSpec` | `append`, `query(filter_spec, *, limit, offset)`, `purge_before(threshold)` |
| `StatefulRepository[T, ID, State]` | `T`, `ID`, `State` | `save`, `get`, `delete`, `transition_if(entity_id, from_state, to_state, **updates)` |
| `MVCCRepository[T, ID, Op]` | `T`, `ID`, `Op` | `append_op`, `snapshot_at(timestamp)`, `get`, `retract`, `get_operation_log` |

All methods are `async def`. All protocols are `@runtime_checkable`.

### Composition

Concrete protocols inherit one or more generics. Python has no
intersection-type syntax, so multi-inheritance is the mechanism:

```python
class TaskRepository(
    IdKeyedRepository[Task, NotBlankStr],
    FilteredQueryRepository[Task, TaskFilterSpec],
):
    ...

class ApprovalRepository(
    StatefulRepository[ApprovalItem, NotBlankStr, ApprovalStatus],
    FilteredQueryRepository[ApprovalItem, ApprovalFilterSpec],
):
    async def save_many(
        self, items: tuple[ApprovalItem, ...]
    ) -> None:
        # Bespoke bulk-insert optimisation; documented under D7.
        ...
```

### Composite keys (D8)

Settings and CircuitBreakerState use composite keys. No dedicated
`CompositeKeyedRepository` category exists; instead the `ID` type
parameter binds to a tuple:

```python
class SettingsRepository(
    IdKeyedRepository[Setting, tuple[NotBlankStr, NotBlankStr]],
):
    ...
```

Concrete classes MAY add ergonomic overloads (`async def get(self,
namespace: NotBlankStr, key: NotBlankStr) -> ...`) alongside the
tuple-keyed generic surface as long as both call into the same
underlying SQL.

### Bespoke methods policy (D7)

Concrete protocols MAY add non-generic methods alongside the inherited
generics when they encode:

1. A real performance optimisation that the generic surface cannot
   express efficiently (e.g. `User.get_by_username` against an indexed
   username column; `Approval.save_many` for batch inserts;
   `CircuitBreakerState.load_all` to warm the in-memory cache at
   startup).
2. A domain invariant that callers must not bypass (e.g.
   `OrgFact.retract` instead of `delete` to preserve the audit
   trail).

A bespoke method MUST NOT exist when its only justification is
familiarity (e.g. keeping `list_tasks` because the old code used that
name): rename callsites to `query(TaskFilterSpec(...))` instead.

## Inventory

The following table captures every protocol class in
`src/synthorg/persistence/` (38 files, 44 classes by file-cluster
expansion). The "Phase" column marks when each one migrates to the
generic composition under WP-1.

### Phase 1 (highest-impact, 14 protocols)

The four named in the issue (Task, Message, CostRecord, Approval) are
mandatory; the remaining ten cover the rest of the generic-category
matrix so Phase 2 has a copy-paste template per category.

| # | Protocol | Composition | Bespoke |
|---|---|---|---|
| 1 | TaskRepository | IdKeyed + FilteredQuery | -- |
| 2 | MessageRepository | AppendOnly | -- |
| 3 | CostRecordRepository | AppendOnly | `aggregate(agent_id, task_id)` |
| 4 | ApprovalRepository | Stateful + FilteredQuery | `save_many` |
| 5 | ProjectRepository | IdKeyed + FilteredQuery | `create_if_absent` (CAS variant) |
| 6 | UserRepository | IdKeyed + FilteredQuery | `get_by_username` |
| 7 | AuditRepository | AppendOnly | -- |
| 8 | WorkflowDefinitionRepository | IdKeyed | `update_if_exists` (CAS variant) |
| 9 | CheckpointRepository | AppendOnly | `get_latest` |
| 10 | OrgFactRepository | MVCC | -- |
| 11 | FineTuneRunRepository | Stateful | -- |
| 12 | SettingsRepository | IdKeyed (`tuple[NotBlankStr, NotBlankStr]`) | `get_namespace`, `delete_namespace` |
| 13 | ProviderAuditRepo | AppendOnly | -- |
| 14 | PresetOverrideRepo | IdKeyed | -- |

### Phase 2 (long tail, ~24 protocols)

Migrated by category recipe, one commit per protocol:

| Group | Protocols |
|---|---|
| AppendOnly | EscalationQueueRepository, WebhookReceiptRepository, OntologyDriftReportRepository, IdempotencyRepository (outcome events) |
| Singleton or composite-singleton | AgentStateRepository, PrincipleOverrideRepository, RiskOverrideRepository, ParkedContextRepository, OAuthStateRepository, SeenClaimsRepository, CircuitBreakerStateRepository, HeartbeatRepository |
| IdKeyed + FilteredQuery | ArtifactRepository, ConnectionRepository, CustomRuleRepository, McpInstallationRepository, SubworkflowRepository, TrainingPlanRepository, TrainingResultRepository, SessionRepository, OntologyEntityRepository, WorkflowExecutionRepository, PersonalityPresetRepository, VersionRepository[T] |
| Auth | LockoutRepository, RefreshTokenRepository, ConnectionSecretRepository |
| Stateful | FineTuneCheckpointRepository, DecisionRepository |

## Migration mechanics

For each protocol:

1. Define a frozen `<Entity>FilterSpec` Pydantic model in the same file
   when filtered queries exist. `extra="forbid"`.
2. Update the protocol class to inherit from the appropriate generics
   and keep bespoke methods that meet the D7 criteria.
3. Update the SQLite and Postgres implementations to expose the
   generic method surface. Rename `list_<entities>` to `list_items`
   and `query`. Drop `find_by_*` finders that fail the D7 criteria;
   fold their callsites into `query(<FilterSpec>(<field>=...))`.
4. Update the conformance test at
   `tests/conformance/persistence/test_<entity>_repo.py` to assert
   against the generic method names.
5. Update every callsite. Run `uv run mypy src/ tests/` to catch
   anything missed.

### Callsite patterns

Filter-by-arg becomes `query(FilterSpec(...))`:

```python
# old
tasks = await backend.tasks.list_tasks(status=TaskStatus.PENDING, project="p1")
# new
tasks = await backend.tasks.query(TaskFilterSpec(status=TaskStatus.PENDING, project="p1"))
```

CAS rename:

```python
# old
ok = await backend.approvals.expire_if_pending(approval_id)
# new
ok = await backend.approvals.transition_if(
    approval_id,
    from_state=ApprovalStatus.PENDING,
    to_state=ApprovalStatus.EXPIRED,
    expired_at=now,
)
```

D7-compliant bespoke methods are unchanged:

```python
user = await backend.users.get_by_username(NotBlankStr("alice"))
```

## Compat scope

None. SynthOrg is pre-alpha; renames apply across the codebase in the
same commit that touches the protocol surface. No deprecation
passthroughs, no aliases.

## Alternatives considered

* **Keep status quo (44 bespoke protocols)**. Rejected: drift is
  already visible (`list_tasks` vs `query` vs `get_history`) and the
  cost of consolidation grows monotonically with each new entity.
* **Four categories without `StatefulRepository` and
  `MVCCRepository`**. Rejected: CAS transitions and MVCC are
  structurally distinct from CRUD; folding them into IdKeyed loses the
  atomicity guarantee that callers depend on.
* **Five categories merging Singleton into IdKeyed-with-Unit-key**.
  Rejected: the API surface of a singleton (`get()` with no args) is
  meaningfully simpler at the callsite than an id-keyed equivalent
  forced to pass `()` or `None`.
* **Dedicated `CompositeKeyedRepository[T, *Keys]` category**.
  Rejected: variadic-tuple type vars (PEP 646) are still rough at the
  edges in mypy strict; `IdKeyedRepository[T, tuple[str, str]]` is
  clearer and works today.

## Consequences

* Reviewability: ~38 protocol files touched in one PR. Commits are
  granular (one per protocol) so reviewers can read commit-by-commit.
* Mypy strict: validated by the Phase 1 first migration; if Protocol
  composition trips strictness, the per-method redeclaration fallback
  is documented in the migration recipe.
* Conformance tests: the parametrised `backend` fixture continues to
  work; the test bodies become more uniform per category.
* Out of scope: DB schemas (Python interface refactor only); web /
  CLI callsites beyond the typing ripple; telemetry events.
