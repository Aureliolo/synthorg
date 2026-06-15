"""Durable cost-claim dedup repository protocol (restart-safe billing).

Backstop against double-billing after a process restart. ``CostTracker``
deduplicates accepted ``CostRecord.claim_id`` values in an in-memory LRU,
but that LRU is empty after a crash/OOM/container restart, so a JetStream
redelivery of an already-billed cost event would otherwise re-run
``ProjectCostAggregateRepository.increment`` and double-charge the
project. This repository persists the same ``claim_id`` guard so the
durable check survives a restart. The contract mirrors
:class:`~synthorg.persistence.seen_claims_protocol.SeenClaimsRepository`:
``has_seen`` is a read-only existence check, ``mark_seen`` records a
terminal billing outcome, and ``prune_expired`` reclaims aged rows.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class ProjectCostClaimSeenRepository(Protocol):
    """Read + atomic-write dedup for project-cost-claim ingestion."""

    async def has_seen(
        self,
        *,
        claim_id: NotBlankStr,
    ) -> bool:
        """Return ``True`` if a row for ``claim_id`` exists.

        Called before a durable project-cost increment to decide whether
        the same ``claim_id`` was already billed on an earlier delivery
        (including before a process restart). A row is written only after
        a successful ``increment``, so a hit here is unambiguous evidence
        the claim was already applied and the current delivery is a
        duplicate.

        Args:
            claim_id: Globally unique cost-record claim identifier.

        Returns:
            ``True`` if a row exists, ``False`` otherwise.

        Raises:
            QueryError: On underlying DB failure (caller decides whether
                to fail-open or fail-closed).
        """
        ...

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        """Try to record ``claim_id`` as a billed claim.

        Called by ``CostTracker`` AFTER a successful durable increment so
        the next redelivery (lost ack, post-restart replay) observes the
        row and skips re-billing. A failed increment never calls this,
        so the absence of the row lets a retry re-bill as intended.

        Args:
            claim_id: Globally unique cost-record claim identifier.
            project_id: The project the cost was billed to. Stored for
                forensics and pruning scope; not part of the uniqueness
                check (``claim_id`` is the primary key).
            now: Wall-clock timestamp persisted as ``seen_at`` and used
                to derive the row's expiry.
            ttl_seconds: Sliding-window TTL after which the row is
                eligible for pruning. Must exceed the maximum redelivery
                horizon so a duplicate can never arrive after the row is
                gone.

        Returns:
            ``True`` if this is the first time ``claim_id`` has been
            recorded; ``False`` if a prior row already exists.

        Raises:
            QueryError: On underlying DB failure (caller decides whether
                to retry or fail-closed).
        """
        ...

    async def prune_expired(self, now: datetime) -> int:
        """Delete rows whose TTL has elapsed.

        Args:
            now: Current wall-clock time; rows with
                ``seen_at + ttl_seconds < now`` are eligible.

        Returns:
            Count of rows removed.
        """
        ...
