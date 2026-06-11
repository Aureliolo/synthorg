"""``ApprovalStoreProtocol`` -- the approval-store contract shared across layers.

``engine`` (agent execution), ``security`` (interceptors), ``hr``
(hiring/promotion/pruning/training/scaling guards), and ``api`` all
type their dependency on the approval store against this protocol so
no caller needs to know the concrete ``ApprovalStore`` lives in
``synthorg.api.approval_store``.
"""

from typing import Protocol, runtime_checkable

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr


@runtime_checkable
class ApprovalStoreProtocol(Protocol):
    """CRUD + lifecycle contract for an approval-item store.

    Implementations provide an in-memory cache with optional
    persistence-backed writes. Consumers depend on this protocol so
    the storage implementation can evolve without touching the
    engine, security, or hr layers.

    Methods mirror the public surface of the concrete store; private
    helpers (cache invalidation, expiration checks) are not part of
    the contract.
    """

    async def clear(self) -> None:
        """Reset all approval items, holding the store lock."""
        ...

    async def add(self, item: ApprovalItem) -> None:
        """Add a new approval item.

        Raises:
            ConflictError: If an item with the same ID already exists.
        """
        ...

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Remove a single approval item by id.

        Returns ``True`` iff a row was removed. Used by callers that
        need to compensate a partial write (e.g. multi-proposal
        parking) without restarting the whole store.
        """
        ...

    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None:
        """Get an approval item by ID, applying lazy expiration."""
        ...

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items with optional filters."""
        ...

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        """Update an existing approval item (first-writer-wins)."""
        ...

    async def save_if_pending(
        self,
        item: ApprovalItem,
    ) -> ApprovalItem | None:
        """Conditionally update an approval item if it is still pending."""
        ...

    async def consume_if_approved(
        self,
        approval_id: NotBlankStr,
    ) -> ApprovalItem | None:
        """Atomically mark an APPROVED one-shot grant as consumed.

        Stamps ``consumed_at`` iff the approval is currently APPROVED and
        not already consumed, so a single grant authorises exactly one
        action (the governed external-access tool calls this before
        egress). Returns the consumed item on success, or ``None`` when
        the approval is missing, not APPROVED, already consumed, or a
        concurrent consume won the race.
        """
        ...


@runtime_checkable
class SyncResettableApprovalStore(Protocol):
    """Test-only escape hatch for sync pytest fixtures.

    Production code MUST depend on :class:`ApprovalStoreProtocol` and
    call its async :meth:`~ApprovalStoreProtocol.clear`. Sync fixtures
    that need to drop approval state without an event loop type their
    dependency on this protocol; concrete stores implement both.

    The class name deliberately avoids the ``Test`` prefix so pytest
    does not attempt to collect the protocol itself.
    """

    def reset_for_test_sync(self) -> None:
        """Synchronous reset that bypasses the store lock."""
        ...
