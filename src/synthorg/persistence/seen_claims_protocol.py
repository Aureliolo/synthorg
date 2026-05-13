"""Worker claim-dedup repository protocol.

Durable backstop for ``WORKERS_DUPLICATE_CLAIM_SUPPRESSED``: workers
consult this repository after a JetStream redelivery so a previously-
completed claim is acked-and-skipped instead of being re-executed.
The contract is intentionally minimal: ``is_completed`` is a read-only
existence check that returns ``True`` only when ``mark_seen`` has
previously recorded a terminal outcome for the same idempotency key.
"""

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class SeenClaimsRepository(Protocol):
    """Read + atomic-write dedup for worker claim ingestion."""

    async def is_completed(
        self,
        *,
        idempotency_key: NotBlankStr,
    ) -> bool:
        """Return ``True`` if a row for ``idempotency_key`` exists.

        Called before executing a claim to decide whether the work has
        already finished on an earlier delivery. A row is written only
        after a terminal outcome (``mark_seen`` is invoked after the
        executor returns ``SUCCESS`` or ``FAILED``), so a hit here is
        unambiguous evidence the claim completed previously and the
        current delivery is a duplicate.

        Args:
            idempotency_key: Globally unique key generated at claim
                publish time.

        Returns:
            ``True`` if a row exists, ``False`` otherwise.

        Raises:
            QueryError: On underlying DB failure (caller decides
                whether to fail-open or fail-closed).
        """
        ...

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: AwareDatetime,
        ttl_seconds: float,
    ) -> bool:
        """Try to record ``idempotency_key`` as a completed claim.

        Called by the worker AFTER a terminal outcome
        (``SUCCESS``/``FAILED``) so the next JetStream redelivery (lost
        ack, slow finalisation) observes the row and ack-and-skips.
        ``RETRY`` outcomes never call this method; the absence of the
        row lets the redelivered claim re-execute as intended.

        Args:
            idempotency_key: Globally unique key generated at claim
                publish time (UUID).
            claim_id: The task_id the claim refers to. Stored for
                forensics; not used for the uniqueness check.
            now: Wall-clock timestamp the caller wants persisted as
                ``seen_at`` (also used to derive the row's TTL).
            ttl_seconds: Sliding-window TTL after which the row is
                eligible for pruning. Callers must set this to at least
                ``ack_wait * max_deliver`` so JetStream can never
                redeliver beyond the dedup horizon.

        Returns:
            ``True`` if this is the first time ``idempotency_key`` has
            been recorded; ``False`` if a prior row exists (the row
            was already written by a concurrent worker that completed
            the same claim first).

        Raises:
            QueryError: On underlying DB failure (caller decides
                whether to retry or fail-closed).
        """
        ...

    async def prune_expired(self, now: AwareDatetime) -> int:
        """Delete rows whose TTL has elapsed.

        Args:
            now: Current wall-clock time; rows with
                ``seen_at + ttl_seconds < now`` are eligible.

        Returns:
            Count of rows removed.
        """
        ...
