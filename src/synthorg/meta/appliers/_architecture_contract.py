"""Architecture applier contract and result types.

This leaf module keeps validators, appliers, and durable contexts decoupled:
validators type against the ``ArchitectureApplierContext`` protocol without
importing the concrete applier, while apply paths share the undo closure and
the serialisable rollback carrier.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from synthorg.meta.models import ArchitectureChange, RollbackOperation

#: Undo closure returned alongside an applied architecture change. Calling it
#: reverses exactly the one change it was produced for (delete a created role or
#: department / re-save a removed role / re-create a removed department / restore
#: a prior workflow definition), so the applier can roll back a partially-applied
#: proposal in reverse order.
ArchitectureUndo = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AppliedArchitectureChange:
    """One durably-applied architecture change and how to reverse it.

    ``undo`` is the in-memory closure the applier calls to reverse a
    partially-applied proposal during the apply call itself.
    ``rollback_operation`` is the serialisable inverse the rollback
    executor dispatches later (on an auto-rollback after a regression),
    carrying the apply-time-captured target (``role:`` / ``workflow:`` /
    ``department:`` prefixed) and ``previous_value`` a statically-authored
    proposal plan cannot know in advance.
    """

    undo: ArchitectureUndo
    rollback_operation: RollbackOperation


@runtime_checkable
class ArchitectureApplierContext(Protocol):
    """Registry view + durable write seam for the architecture applier.

    The read methods (sync) back ``dry_run`` validation; ``apply_change``
    (async) backs the real ``apply`` path, returning a per-change undo closure
    so a partially-applied proposal can be rolled back in reverse order.
    """

    def has_role(self, name: str) -> bool:
        """Return True when a role with ``name`` is registered."""
        ...

    def has_department(self, name: str) -> bool:
        """Return True when a department with ``name`` is registered."""
        ...

    def has_workflow(self, name: str) -> bool:
        """Return True when a workflow with ``name`` is registered."""
        ...

    def role_in_use(self, name: str) -> bool:
        """Return True when removing the role would dangle references."""
        ...

    def department_in_use(self, name: str) -> bool:
        """Return True when removing the department would dangle references."""
        ...

    async def apply_change(
        self, change: ArchitectureChange
    ) -> AppliedArchitectureChange:
        """Durably apply one architecture change.

        Returns:
            The applied change's in-memory undo closure plus the
            serialisable inverse operation for later auto-rollback.

        Raises:
            Exception: On a durable-write failure (the applier rolls back
                the already-applied changes).
        """
        ...

    async def refresh_snapshot(self) -> None:
        """Reload the cached read snapshot after a successful apply."""
        ...
