"""Generic repository protocol categories for persistence layer composition.

Entity repository protocols compose from six generic categories via
Protocol inheritance::

    class TaskRepository(
        IdKeyedRepository[Task, NotBlankStr],
        FilteredQueryRepository[Task, TaskFilterSpec],
    ): ...

The six categories cover every persistence pattern in the codebase:

* ``SingletonRepository[T]`` -- one-row global state.
* ``IdKeyedRepository[T, ID]`` -- CRUD by primary key (composite keys
  use ``ID = tuple[str, ...]``).
* ``FilteredQueryRepository[T, FilterSpec]`` -- multi-row queries with
  a typed ``FilterSpec`` args model; always composed alongside
  ``IdKeyedRepository``.
* ``AppendOnlyRepository[Event, FilterSpec]`` -- immutable event logs
  with query + retention purge.
* ``StatefulRepository[T, ID, State]`` -- ``IdKeyedRepository`` plus
  ``transition_if`` for atomic state-machine transitions (CAS).
* ``MVCCRepository[T, ID, Op]`` -- append-only operation log plus
  point-in-time snapshots and non-destructive retraction.

See ``docs/decisions/0001-repository-protocol-consolidation.md`` for the
full RFC including composition rules, the bespoke-method policy
(concrete protocols may add non-generic methods when they encode a real
perf optimisation or domain invariant), and the per-entity inventory.

All protocols are ``@runtime_checkable`` so ``isinstance`` works in
tests; all methods are ``async def`` because SynthOrg's persistence
layer is uniformly async.
"""

from datetime import datetime  # noqa: TC003 -- referenced by Protocol signatures
from typing import Final, Protocol, TypeVar, runtime_checkable

#: Canonical page size for ``list_items`` / ``query`` across every
#: repository protocol defined here and every concrete repo that
#: composes one of them. Pinned in one place so callers and impls
#: cannot drift apart. The ``# lint-allow: magic-numbers`` opt-out on
#: every protocol method default below references this constant so the
#: justification is not duplicated per method body.
DEFAULT_PAGE_SIZE: Final[int] = 100

# Variance follows the position rule: TypeVars that only appear in
# argument position are contravariant; TypeVars that only appear in
# return position are covariant; TypeVars that appear in both are
# invariant. Pyright enforces this for ``Protocol`` types.
T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
ID_contra = TypeVar("ID_contra", contravariant=True)
FilterSpec_contra = TypeVar("FilterSpec_contra", contravariant=True)
Event = TypeVar("Event")
State_contra = TypeVar("State_contra", contravariant=True)
Op = TypeVar("Op")


@runtime_checkable
class SingletonRepository(Protocol[T]):
    """One-row global state repository.

    Used for state that has exactly one row total (e.g. server-wide
    configuration snapshots). Keyed-singleton variants (Settings,
    AgentState) use :class:`IdKeyedRepository` with ``ID = NotBlankStr``
    or ``ID = tuple[NotBlankStr, ...]`` instead.

    Invariant: the single-row constraint is enforced by concrete
    implementations (e.g. a CHECK on a fixed primary key, or upsert
    semantics on ``upsert``). The Protocol cannot enforce it
    structurally, so concrete repos that compose this surface MUST
    guarantee it at the storage layer.

    ``T`` SHOULD be immutable (Pydantic ``frozen=True`` model,
    ``FrozenDataclass``, or equivalent) so callers cannot mutate the
    object returned by :meth:`get` and corrupt repository state.
    """

    async def get(self) -> T | None:
        """Read the singleton, or ``None`` when uninitialised."""
        ...

    async def upsert(self, value: T) -> None:
        """Insert or replace the singleton row."""
        ...

    async def delete(self) -> bool:
        """Delete the singleton row. Return ``True`` iff a row existed."""
        ...


@runtime_checkable
class IdKeyedRepository(Protocol[T, ID_contra]):
    """CRUD by primary key.

    Composite keys are expressed via ``ID = tuple[NotBlankStr, ...]``;
    no separate ``CompositeKeyedRepository`` exists (see ADR-0001 D8).

    ``T`` SHOULD be immutable (Pydantic ``frozen=True`` model,
    ``FrozenDataclass``, or equivalent) so the tuple returned by
    :meth:`list_items` cannot be mutated by callers in ways that
    desynchronise their view from the storage layer.
    """

    async def save(self, entity: T) -> None:
        """Insert or update an entity (idempotent upsert)."""
        ...

    async def get(self, entity_id: ID_contra) -> T | None:
        """Retrieve an entity by id, or ``None`` when absent."""
        ...

    async def delete(self, entity_id: ID_contra) -> bool:
        """Delete an entity by id. Return ``True`` iff a row existed."""
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[T, ...]:
        """List entities with pagination, ordered deterministically by id."""
        ...


@runtime_checkable
class FilteredQueryRepository(Protocol[T_co, FilterSpec_contra]):
    """Multi-row query with a typed ``FilterSpec`` args model.

    Always composed alongside :class:`IdKeyedRepository` for entities
    that support both id lookup and filtered enumeration.
    """

    async def query(
        self,
        filter_spec: FilterSpec_contra,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[T_co, ...]:
        """Return all entities matching the filter spec (paginated).

        Order: each concrete repo documents its ordering invariant in
        its own protocol docstring (typically primary-key ascending or
        a domain-specific column). Pagination is best-effort snapshot
        stable: concurrent writes between two calls may shift offset
        boundaries, so callers that need transactional pagination
        should consume the full result inside one logical batch.
        Callers that need a different order should sort the returned
        tuple in the caller rather than embedding sort hints in
        ``FilterSpec``.
        """
        ...

    async def count(self, filter_spec: FilterSpec_contra) -> int:
        """Return the number of entities matching the filter spec."""
        ...


@runtime_checkable
class AppendOnlyRepository(Protocol[Event, FilterSpec_contra]):
    """Immutable event log with query and retention purge.

    No per-row update or delete; ``purge_before`` is the only deletion
    primitive and is restricted to bulk retention sweeps. A small
    number of concrete repos add a per-row ``delete`` as a bespoke
    method under ADR-0001 D7 (e.g. operator-driven moderation on
    ``MessageRepository``); such bespoke methods are domain-specific
    and not part of the generic surface here.

    ``Event`` SHOULD be immutable (frozen Pydantic / dataclass) so
    historical records cannot be retroactively mutated by callers.
    """

    async def append(self, event: Event) -> None:
        """Append one event (write-only; events are immutable once written)."""
        ...

    async def query(
        self,
        filter_spec: FilterSpec_contra,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Event, ...]:
        """Return events matching the filter spec, newest-first (paginated).

        Order is fixed: append-only logs return rows by descending
        append timestamp / id so a paginated walk yields the most
        recent activity first without per-repo configuration. Note
        this is the *opposite* of :class:`FilteredQueryRepository`,
        which defaults to ascending primary-key order; the two
        protocols intentionally diverge because audit-style consumers
        almost always want recency first.
        """
        ...

    async def purge_before(self, threshold: datetime) -> int:
        """Delete events older than ``threshold``. Returns rows removed."""
        ...


@runtime_checkable
class StatefulRepository(Protocol[T, ID_contra, State_contra]):
    """IdKeyed + atomic compare-and-set status transitions.

    ``transition_if`` is the structural distinction from
    :class:`IdKeyedRepository`: it must perform the read-and-write
    atomically (a CAS at the DB level) so two concurrent callers
    cannot both observe ``from_state`` and both write ``to_state``.

    ``**updates`` carries status-correlated fields (e.g.
    ``expired_at``, ``expired_by``). Concrete repos MUST declare a
    per-repo ``TypedDict`` for the accepted keys (so callers get
    static type-checker enforcement) and MUST validate kwargs at the
    implementation layer (so passing an unknown key raises rather
    than being silently dropped). See ``ApprovalRepository`` for the
    canonical example.
    """

    async def save(self, entity: T) -> None:
        """Insert or update an entity."""
        ...

    async def get(self, entity_id: ID_contra) -> T | None:
        """Retrieve an entity by id."""
        ...

    async def delete(self, entity_id: ID_contra) -> bool:
        """Delete an entity by id. Return ``True`` iff a row existed."""
        ...

    async def transition_if(
        self,
        entity_id: ID_contra,
        from_state: State_contra,
        to_state: State_contra,
        **updates: object,
    ) -> bool:
        """Atomic CAS: move ``entity_id`` from ``from_state`` to ``to_state``.

        Returns ``True`` iff the row was in ``from_state`` and is now
        in ``to_state``. Returns ``False`` on state mismatch or when
        no row exists.

        ``**updates`` carries status-correlated columns; concrete repos
        MUST type the kwargs with a ``TypedDict`` and MUST reject
        unknown keys at the implementation layer. ``object`` here is
        the widest possible declaration so the Protocol stays usable
        for every concrete state schema; do not interpret it as a
        promise that any key will be accepted.
        """
        ...


@runtime_checkable
class MVCCRepository(Protocol[T_co, ID_contra, Op]):
    """Append-only operation log plus point-in-time snapshots.

    The only concrete consumer today is ``OrgFactRepository``; the
    pattern is reusable for any auditable knowledge store.
    """

    async def append_op(self, op: Op) -> None:
        """Append one operation (immutable log entry)."""
        ...

    async def snapshot_at(self, timestamp: datetime) -> tuple[T_co, ...]:
        """Return entity state as of ``timestamp``."""
        ...

    async def get(self, entity_id: ID_contra) -> T_co | None:
        """Return the current (latest) state of an entity by id."""
        ...

    async def retract(self, entity_id: ID_contra, reason: str) -> None:
        """Non-destructive delete: append a tombstone op with ``reason``."""
        ...

    async def get_operation_log(self, entity_id: ID_contra) -> tuple[Op, ...]:
        """Return the full op history for one entity.

        Ordering: rows are returned by ascending append order
        (oldest-first). The append-order key is the storage layer's
        monotonic insertion id; concrete implementations MUST not
        re-order historical entries even after retraction, since the
        log is the system-of-record for the entity's causal history.
        """
        ...
