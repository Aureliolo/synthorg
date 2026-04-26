"""Idempotency-key repository protocol and value types.

Persistent idempotency keys let retry-prone endpoints (webhook
receivers, backup triggers, evaluation triggers) survive process
restart without emitting duplicate side effects on retry. The
in-memory ``ReplayProtector`` still acts as the cheap pre-filter for
timestamp-window enforcement; this protocol provides the durable
backstop for cross-restart and cross-replica deduplication.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from datetime import datetime


class IdempotencyOutcome(StrEnum):
    """The result of attempting to claim an idempotency key.

    ``FRESH`` -- no record existed; the caller has claimed the key
    and must execute the underlying operation.
    ``IN_FLIGHT`` -- another worker holds the claim; the caller
    should retry after backoff or short-circuit with 409.
    ``COMPLETED`` -- the operation finished successfully on a prior
    request; the cached response is returned verbatim.
    ``FAILED`` -- the prior attempt errored; the caller may re-claim
    the key (the ``failed`` row is treated as if it had expired).
    """

    FRESH = "fresh"
    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"
    FAILED = "failed"


class IdempotencyClaim(BaseModel):
    """Outcome of an atomic claim attempt.

    Attributes:
        outcome: Discriminator for what the caller should do next.
        cached_response: When ``outcome`` is ``COMPLETED``, the JSON
            string body returned by the prior successful execution.
            ``None`` for every other outcome.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    outcome: IdempotencyOutcome
    cached_response: str | None = Field(default=None)


class IdempotencyRecord(BaseModel):
    """A persisted idempotency-key row.

    Attributes:
        scope: Namespace separating different callers
            (e.g. ``webhooks:github`` vs ``backup``) so keys do not
            collide across endpoints.
        key: Caller-supplied idempotency token.
        status: Current lifecycle state.
        response_hash: SHA-256 of the cached response body, or
            ``None`` while the record is in-flight or failed.
        response_body: JSON-encoded cached response, or ``None``
            until the operation completes successfully.
        created_at: When the claim was first inserted.
        expires_at: When the row becomes eligible for cleanup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    scope: NotBlankStr
    key: NotBlankStr
    status: IdempotencyOutcome
    response_hash: str | None = None
    response_body: str | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime


@runtime_checkable
class IdempotencyRepository(Protocol):
    """Atomic claim-and-cache primitive for retry-safe endpoints."""

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: datetime,
    ) -> IdempotencyClaim:
        """Attempt to claim *(scope, key)* for the duration of *ttl_seconds*.

        Atomic in two senses: only one of N concurrent callers
        receives ``FRESH``, and the underlying database insert /
        select runs inside a single transaction so the discriminator
        cannot race.
        """
        ...

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
    ) -> None:
        """Mark a claimed key as ``COMPLETED`` and store the response."""
        ...

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> None:
        """Mark a claimed key as ``FAILED`` so future retries can re-claim."""
        ...

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        """Fetch the persisted record verbatim (None if absent)."""
        ...

    async def cleanup_expired(self, now: datetime) -> int:
        """Delete expired rows. Returns the number of rows removed."""
        ...
