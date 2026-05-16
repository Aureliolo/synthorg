"""Risk override repository protocol."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.types import NotBlankStr
    from synthorg.security.rules.risk_override import RiskTierOverride


@runtime_checkable
class RiskOverrideRepository(
    IdKeyedRepository["RiskTierOverride", "NotBlankStr"],
    Protocol,
):
    """CRUD for risk tier overrides.

    Composes :class:`IdKeyedRepository` (ADR-0001). ``save`` here
    diverges from the generic upsert semantics on purpose: each
    override row is an immutable audit artefact, so a second call with
    the same id raises ``DuplicateRecordError`` rather than silently
    updating. Bespoke per D7: :meth:`list_active` filters by
    non-expired + non-revoked + ordered ``created_at`` DESC for the
    policy hot path; :meth:`revoke` is a CAS state transition with
    correlated columns that ``IdKeyedRepository`` cannot express.
    """

    async def save(self, entity: RiskTierOverride) -> None:
        """Persist a new override (insert-only).

        Args:
            entity: The override to save.

        Raises:
            DuplicateRecordError: If an override with the same ID exists.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> RiskTierOverride | None:
        """Retrieve an override by ID.

        Args:
            entity_id: The override identifier.

        Returns:
            The override, or None if not found.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[RiskTierOverride, ...]:
        """List overrides in id order (generic IdKeyed surface).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.
        """
        ...

    async def list_active(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[RiskTierOverride, ...]:
        """Return active (non-expired, non-revoked) overrides.

        Args:
            limit: Maximum overrides to return.

        Returns:
            Tuple of active overrides ordered by created_at DESC,
            capped at *limit* rows.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an override by ID.

        Args:
            entity_id: The override identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.
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
