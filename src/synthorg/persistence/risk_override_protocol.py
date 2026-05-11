"""Risk override repository protocol."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.types import NotBlankStr
    from synthorg.security.rules.risk_override import RiskTierOverride


@runtime_checkable
class RiskOverrideRepository(Protocol):
    """CRUD for risk tier overrides.

    Provides persistence for ``RiskTierOverride`` instances with
    query support for active (non-expired, non-revoked) overrides.
    """

    async def save(self, override: RiskTierOverride) -> None:
        """Persist a new override.

        Args:
            override: The override to save.

        Raises:
            DuplicateRecordError: If an override with the same ID exists.
        """
        ...

    async def get(self, override_id: NotBlankStr) -> RiskTierOverride | None:
        """Retrieve an override by ID.

        Args:
            override_id: The override identifier.

        Returns:
            The override, or None if not found.
        """
        ...

    async def list_active(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[RiskTierOverride, ...]:
        """Return active (non-expired, non-revoked) overrides.

        Args:
            limit: Maximum overrides to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Tuple of active overrides ordered by created_at DESC,
            capped at *limit* rows.
        """
        ...

    async def revoke(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr,
        revoked_at: AwareDatetime,
    ) -> bool:
        """Mark an override as revoked.

        Args:
            override_id: The override to revoke.
            revoked_by: User who revoked it.
            revoked_at: When it was revoked.

        Returns:
            True if the override was found and revoked, False otherwise.
        """
        ...
