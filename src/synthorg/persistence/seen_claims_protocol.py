"""Worker claim-dedup repository protocol.

Durable backstop for ``WORKERS_DUPLICATE_CLAIM_SUPPRESSED``: workers
consult this repository before processing a ``TaskClaim`` so a
JetStream redelivery (ack lost in transit, worker crash before ack)
cannot trigger a second execution. The contract is intentionally
minimal: first-write returns ``True``, every subsequent write of the
same ``idempotency_key`` within the TTL returns ``False``.
"""

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class SeenClaimsRepository(Protocol):
    """Atomic first-write dedup for worker claim ingestion."""

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: AwareDatetime,
        ttl_seconds: float,
    ) -> bool:
        """Try to mark ``idempotency_key`` as seen.

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
            been recorded; ``False`` if a prior row exists (caller
            must ack-and-skip).

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
