"""``ApprovalStoreProtocol`` -- the approval-store contract shared across layers.

``engine`` (agent execution), ``security`` (interceptors), ``hr``
(hiring/promotion/pruning/training/scaling guards), and ``api`` all
type their dependency on the approval store against this protocol so
no caller needs to know the concrete ``ApprovalStore`` lives in
``synthorg.api.approval_store``.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.approval import ApprovalItem
    from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
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
        """Reset all approval items, holding the store lock (#1599)."""
        ...

    def reset_for_test_sync(self) -> None:
        """Synchronous reset for sync pytest fixtures only.

        Bypasses the store lock; production code MUST call ``clear``.
        """
        ...

    async def add(self, item: ApprovalItem) -> None:
        """Add a new approval item.

        Raises:
            ConflictError: If an item with the same ID already exists.
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
