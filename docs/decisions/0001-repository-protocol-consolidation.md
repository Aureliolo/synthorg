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
        self, items: Sequence[ApprovalItem]
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

The following comprehensive table captures every protocol class in
`src/synthorg/persistence/`, `src/synthorg/communication/`, and `src/synthorg/hr/`
(50+ classes across 40+ files). Each row shows which generic categories the protocol
inherits and which D7-compliant bespoke methods are kept. Four protocols do not
compose any generic category and are documented at the end as "bespoke per D7".

### Per-Entity Composition and Bespoke Methods

| # | Protocol | Location | Composition | Bespoke D7 Methods | Reason |
|---|---|---|---|---|---|
| 1 | TaskRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 2 | MessageRepository | persistence/ | AppendOnly | -- | Append-only message log |
| 3 | CostRecordRepository | persistence/ | AppendOnly | `aggregate` | Perf: SUM(cost) with multi-key filters |
| 4 | ApprovalRepository | persistence/ | Stateful + FilteredQuery | `save_many`, `expire_if_pending` | Bulk insert + CAS state transition |
| 5 | ProjectRepository | persistence/ | IdKeyed + FilteredQuery | `create_if_absent` | CAS: INSERT OR SKIP idempotency |
| 6 | UserRepository | persistence/ | IdKeyed + FilteredQuery | `get_by_username` | Indexed lookup on username column |
| 7 | AuditRepository | persistence/ | AppendOnly | `purge_before` | Retention sweep (exception to append-only rule) |
| 8 | WorkflowDefinitionRepository | persistence/ | IdKeyed + FilteredQuery | `create_if_absent`, `update_if_exists` | CAS variants for distinct audit semantics |
| 9 | CheckpointRepository | persistence/ | AppendOnly | `get_latest`, `delete_by_execution` | Domain: latest by turn_number; cleanup by execution |
| 10 | HeartbeatRepository | persistence/ | Singleton (per execution) | `get_stale` | Domain: stale-timeout queries for cleanup |
| 11 | OrgFactRepository | persistence/ | MVCC | -- | Point-in-time snapshot + operation log |
| 12 | FineTuneRunRepository | persistence/ | Stateful | `get_active_run`, `mark_interrupted` | Domain: active-run singleton per manager |
| 13 | SettingsRepository | persistence/ | IdKeyed (composite) | `get_namespace`, `delete_namespace` | Namespace-level bulk operations |
| 14 | ProviderAuditRepository | persistence/ | AppendOnly | -- | Append-only provider evaluation log |
| 15 | PresetOverrideRepository | persistence/ | Singleton (per entity) | -- | One override record per preset ID |
| 16 | PresetRepository | persistence/ | IdKeyed + FilteredQuery | `count` | Standard CRUD with count aggregate |
| 17 | AgentStateRepository | persistence/ | IdKeyed | `get_active` | Domain: non-idle agent states only |
| 18 | ArtifactRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 19 | ConnectionRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 20 | CustomRuleRepository | persistence/ | IdKeyed + FilteredQuery | `get_by_name` | Indexed lookup on rule name |
| 21 | McpInstallationRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 22 | SubworkflowRepository | persistence/ | IdKeyed + FilteredQuery | `find_parents`, `delete_if_unreferenced` | Domain: parent-child graph operations |
| 23 | TrainingPlanRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 24 | TrainingResultRepository | persistence/ | IdKeyed + FilteredQuery | `latest_by_agent` | Domain: most recent result per agent |
| 25 | SessionRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 26 | OntologyEntityRepository | persistence/ | IdKeyed + FilteredQuery | `search` | Text search on indexed content |
| 27 | OntologyDriftReportRepository | persistence/ | AppendOnly | -- | Append-only drift report log |
| 28 | WorkflowExecutionRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 29 | PersonalityPresetRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 30 | VersionRepository[T] | persistence/ | IdKeyed + FilteredQuery | `get_by_content_hash` | Indexed lookup on content hash |
| 31 | ParkedContextRepository | persistence/ | Singleton (per agent) | `get_by_approval`, `get_by_agent` | Domain: lookup by approval or agent |
| 32 | PrincipleOverrideRepository | persistence/ | IdKeyed + FilteredQuery | -- | Standard CRUD with filters |
| 33 | RiskOverrideRepository | persistence/ | IdKeyed + FilteredQuery | `revoke` | Domain: mark inactive without delete |
| 34 | CircuitBreakerStateRepository | persistence/ | IdKeyed (composite) | `load_all` | Perf: bulk cache warmth at startup |
| 35 | DecisionRepository | persistence/ | Stateful | -- | State machine: draft, pending, decided |
| 36 | SsrfViolationRepository | persistence/ | IdKeyed + FilteredQuery | `update_status` | Domain: transition finding status |
| 37 | SessionRepository (auth) | persistence/auth_protocol.py | Stateful | -- | Session lifecycle: active, revoked |
| 38 | LockoutRepository (auth) | persistence/auth_protocol.py | Stateful | `record_failure`, `record_success` | Domain: failed-attempt tracking |
| 39 | RefreshTokenRepository (auth) | persistence/auth_protocol.py | Stateful | -- | Token lifecycle: issued, revoked |
| 40 | ConnectionSecretRepository (auth) | persistence/auth_protocol.py | Stateful | `retrieve`, `store` | Domain: encrypted secret storage |
| 41 | FineTuneCheckpointRepository | persistence/ | Stateful | `get_active_checkpoint`, `set_active` | Domain: active checkpoint per run |
| 42 | EscalationQueueRepository | communication/ | **Bespoke per D7** | `create`, `get`, `list_items`, `apply_decision`, `cancel`, `mark_expired`, `subscribe_notifications` | Lifecycle + streaming + state-machine ops do not fit CRUD |
| 43 | IdempotencyRepository | persistence/ | **Bespoke per D7** | `claim`, `complete`, `fail`, `cleanup_expired` | Atomic claim-and-lease with token-guarded CAS; no standard CRUD |
| 44 | ProjectCostAggregateRepository | persistence/ | **Bespoke per D7** | `get`, `increment` | Only get + atomic increment with mixed-currency rejection |
| 45 | SeenClaimsRepository | persistence/ | **Bespoke per D7** | `is_completed`, `mark_seen`, `prune_expired` | Dedup with TTL pruning; no entity model |
| 46 | CeremonySchedulerStateRepository | persistence/ | IdKeyed | `load_all` | WP-1 restart safety: hydrate counters/fired-once flags on sprint activation; perf bulk read on cold start |
| 47 | MeetingCooldownRepository | persistence/ | IdKeyed | `load_all` | WP-1 restart safety: hydrate cooldown timestamps on scheduler start; perf bulk read on cold start |
| 48 | TrackedContainerRepository | persistence/ | IdKeyed | `load_all` | WP-1 restart safety: enumerate sandbox containers for reconciliation on subsystem start |
| 49 | DocsRepository | persistence/docs_protocol.py | IdKeyed (composite) + FilteredQuery | -- | Living-doc metadata; composite `(project_id, slug)` key, filter by doc_type / tag / updated_since |

### Bespoke-Only Protocols (No Generic Composition)

The following four protocols do not inherit from any of the six generic categories.
They remain fully bespoke per D7 because their operation semantics are fundamentally
distinct from CRUD and cannot be expressed as compositions of the generics.

| Protocol | Location | Reason |
|---|---|---|
| **EscalationQueueRepository** | `src/synthorg/communication/conflict_resolution/escalation/protocol.py` | Lifecycle + streaming + state-machine ops (apply_decision, cancel, mark_expired) do not fit CRUD; subscribe_notifications returns an AsyncIterator for real-time cross-instance updates |
| **IdempotencyRepository** | `src/synthorg/persistence/idempotency_protocol.py` | Atomic claim-and-lease with token-guarded CAS; claim/complete/fail form a state machine independent of entity shape and do not expose list/query/delete semantics |
| **ProjectCostAggregateRepository** | `src/synthorg/persistence/project_cost_aggregate_protocol.py` | Only get + atomic increment with mixed-currency rejection; no save/delete/list/query semantics at all |
| **SeenClaimsRepository** | `src/synthorg/persistence/seen_claims_protocol.py` | Dedup with TTL pruning + atomic mark_seen; no entity model, only idempotency-key existence checks and mark-seen CAS |

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

* Reviewability: ~40+ protocol files touched in one PR. Commits are
  granular (one per protocol) so reviewers can read commit-by-commit.
* Mypy strict: validated by the Phase 1 first migration; if Protocol
  composition trips strictness, the per-method redeclaration fallback
  is documented in the migration recipe.
* Conformance tests: the parametrised `backend` fixture continues to
  work; the test bodies become more uniform per category.
* Out of scope: DB schemas (Python interface refactor only); web /
  CLI callsites beyond the typing ripple; telemetry events.
