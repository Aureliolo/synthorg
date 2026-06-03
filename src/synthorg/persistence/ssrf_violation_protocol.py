"""SSRF violation repository protocol."""

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus


@runtime_checkable
class SsrfViolationRepository(Protocol):
    """Append-and-resolve store for SSRF violation records.

    A bespoke protocol rather than a composed :class:`IdKeyedRepository`
    because violation rows are immutable forensic evidence: there is no
    upsert (``save`` is insert-only and raises ``DuplicateRecordError``
    on a repeated id) and no runtime ``delete`` (erasing security
    evidence outside the controlled ``update_status`` flow would break
    the immutability guarantee). Physical deletion exists only on the
    concrete repositories for retention/ops tooling and is deliberately
    kept off this runtime surface. :meth:`list_violations` filters by
    ``status`` ordered by ``timestamp`` DESC for the security review
    queue; :meth:`update_status` is a CAS state transition with
    correlated columns.
    """

    async def save(self, entity: SsrfViolation) -> None:
        """Persist a new SSRF violation (insert-only).

        Args:
            entity: The violation to save.

        Raises:
            DuplicateRecordError: If a violation with the same ID exists.
        """
        ...

    async def get(
        self,
        entity_id: NotBlankStr,
    ) -> SsrfViolation | None:
        """Retrieve a violation by ID.

        Args:
            entity_id: The violation identifier.

        Returns:
            The violation, or None if not found.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        """List violations in id order (generic IdKeyed surface).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.
        """
        ...

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[SsrfViolation, ...]:
        """List violations, optionally filtered by status.

        Args:
            status: Filter by status (None for all).
            limit: Maximum number of results (must be positive).

        Returns:
            Tuple of violations, ordered by timestamp DESC.

        Raises:
            ValueError: If *limit* is not positive.
        """
        ...

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: SsrfViolationStatus,
        resolved_by: NotBlankStr,
        resolved_at: datetime,
    ) -> bool:
        """Update a violation's status (allow or deny).

        Only transitions violations currently in PENDING status.
        Violations already resolved (ALLOWED or DENIED) are not
        updated and the method returns ``False``.

        Args:
            violation_id: The violation to update.
            status: New status (ALLOWED or DENIED, not PENDING).
            resolved_by: User who resolved it.
            resolved_at: When it was resolved.

        Returns:
            ``True`` if a pending violation was found and transitioned,
            ``False`` if the violation was not found or already resolved.

        Raises:
            ValueError: If *status* is PENDING.
        """
        ...
