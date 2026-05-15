"""Generic repository protocol categories for persistence layer composition.

WP-1 consolidates ~44 entity-specific ``*Repository`` protocols into six
generic categories that compose via Protocol inheritance::

    class TaskRepository(
        IdKeyedRepository[Task, NotBlankStr],
        FilteredQueryRepository[Task, TaskFilterSpec],
    ): ...

The six categories cover every persistence pattern present in the
codebase:

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
from typing import Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
ID = TypeVar("ID")
FilterSpec = TypeVar("FilterSpec")
Event = TypeVar("Event")
State = TypeVar("State")
Op = TypeVar("Op")


@runtime_checkable
class SingletonRepository(Protocol[T]):
    """One-row global state repository.

    Used for state that has exactly one row total (e.g. server-wide
    configuration snapshots). Keyed-singleton variants (Settings,
    AgentState) use :class:`IdKeyedRepository` with ``ID = NotBlankStr``
    or ``ID = tuple[NotBlankStr, ...]`` instead.
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
class IdKeyedRepository(Protocol[T, ID]):
    """CRUD by primary key.

    Composite keys are expressed via ``ID = tuple[NotBlankStr, ...]``;
    no separate ``CompositeKeyedRepository`` exists (see ADR-0001 D8).
    """

    async def save(self, entity: T) -> None:
        """Insert or update an entity (idempotent upsert)."""
        ...

    async def get(self, entity_id: ID) -> T | None:
        """Retrieve an entity by id, or ``None`` when absent."""
        ...

    async def delete(self, entity_id: ID) -> bool:
        """Delete an entity by id. Return ``True`` iff a row existed."""
        ...

    async def list_items(self, *, limit: int = 100, offset: int = 0) -> tuple[T, ...]:
        """List entities with pagination, ordered deterministically by id."""
        ...


@runtime_checkable
class FilteredQueryRepository(Protocol[T, FilterSpec]):
    """Multi-row query with a typed ``FilterSpec`` args model.

    Always composed alongside :class:`IdKeyedRepository` for entities
    that support both id lookup and filtered enumeration.
    """

    async def query(
        self,
        filter_spec: FilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[T, ...]:
        """Return all entities matching the filter spec (paginated)."""
        ...

    async def count(self, filter_spec: FilterSpec) -> int:
        """Return the number of entities matching the filter spec."""
        ...


@runtime_checkable
class AppendOnlyRepository(Protocol[Event, FilterSpec]):
    """Immutable event log with query and retention purge.

    No per-row update or delete; ``purge_before`` is the only deletion
    primitive and is restricted to bulk retention sweeps.
    """

    async def append(self, event: Event) -> None:
        """Append one event (write-only; events are immutable once written)."""
        ...

    async def query(
        self,
        filter_spec: FilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Event, ...]:
        """Return events matching the filter spec, newest-first (paginated)."""
        ...

    async def purge_before(self, threshold: datetime) -> int:
        """Delete events older than ``threshold``. Returns rows removed."""
        ...


@runtime_checkable
class StatefulRepository(Protocol[T, ID, State]):
    """IdKeyed + atomic compare-and-set status transitions.

    ``transition_if`` is the structural distinction from
    :class:`IdKeyedRepository`: it must perform the read-and-write
    atomically (a CAS at the DB level) so two concurrent callers
    cannot both observe ``from_state`` and both write ``to_state``.

    ``**updates`` carries status-correlated fields (e.g.
    ``expired_at``, ``expired_by``). Each concrete repo documents the
    keys it accepts.
    """

    async def save(self, entity: T) -> None:
        """Insert or update an entity."""
        ...

    async def get(self, entity_id: ID) -> T | None:
        """Retrieve an entity by id."""
        ...

    async def delete(self, entity_id: ID) -> bool:
        """Delete an entity by id. Return ``True`` iff a row existed."""
        ...

    async def transition_if(
        self,
        entity_id: ID,
        from_state: State,
        to_state: State,
        **updates: object,
    ) -> bool:
        """Atomic CAS: move ``entity_id`` from ``from_state`` to ``to_state``.

        Returns ``True`` iff the row was in ``from_state`` and is now
        in ``to_state``. Returns ``False`` on state mismatch or when
        no row exists.
        """
        ...


@runtime_checkable
class MVCCRepository(Protocol[T, ID, Op]):
    """Append-only operation log plus point-in-time snapshots.

    The only concrete consumer today is ``OrgFactRepository``; the
    pattern is reusable for any auditable knowledge store.
    """

    async def append_op(self, op: Op) -> None:
        """Append one operation (immutable log entry)."""
        ...

    async def snapshot_at(self, timestamp: datetime) -> tuple[T, ...]:
        """Return entity state as of ``timestamp``."""
        ...

    async def get(self, entity_id: ID) -> T | None:
        """Return the current (latest) state of an entity by id."""
        ...

    async def retract(self, entity_id: ID, reason: str) -> None:
        """Non-destructive delete: append a tombstone op with ``reason``."""
        ...

    async def get_operation_log(self, entity_id: ID) -> tuple[Op, ...]:
        """Return the full op history for one entity (oldest-first)."""
        ...
