"""Idempotency-key repository protocol and value types.

Persistent idempotency keys let retry-prone endpoints (webhook
receivers, backup triggers, evaluation triggers) survive process
restart without emitting duplicate side effects on retry. The
in-memory ``ReplayProtector`` still acts as the cheap pre-filter for
timestamp-window enforcement; this protocol provides the durable
backstop for cross-restart and cross-replica deduplication.
"""

from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


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
        claim_token: Random opaque token issued for ``FRESH`` claims so
            ``complete`` / ``fail`` can compare-and-swap against the
            current lease. A stale worker that timed out and finishes
            after another worker re-claimed the key will see the row's
            token has rotated and skip the write, instead of
            overwriting the new lease's cached response. ``None`` for
            outcomes the caller cannot complete (``IN_FLIGHT`` /
            ``COMPLETED`` / ``FAILED``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    outcome: IdempotencyOutcome
    cached_response: str | None = Field(default=None)
    claim_token: NotBlankStr | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_cached_response_matches_outcome(self) -> Self:
        """Enforce: ``cached_response`` is set iff ``outcome`` is COMPLETED.

        Prevents constructing a claim that pretends to have a cached
        body for an in-flight or failed entry, or one that drops the
        cached body for a completed entry. Also enforces that
        ``claim_token`` is set iff outcome is ``FRESH`` -- only the
        FRESH winner has a lease to defend.
        """
        if self.outcome is IdempotencyOutcome.COMPLETED:
            if self.cached_response is None:
                msg = "cached_response must be present when outcome is COMPLETED"
                raise ValueError(msg)
        elif self.cached_response is not None:
            msg = f"cached_response must be None when outcome is {self.outcome.value!r}"
            raise ValueError(msg)
        if self.outcome is IdempotencyOutcome.FRESH:
            if self.claim_token is None:
                msg = "claim_token must be present when outcome is FRESH"
                raise ValueError(msg)
        elif self.claim_token is not None:
            msg = f"claim_token must be None when outcome is {self.outcome.value!r}"
            raise ValueError(msg)
        return self


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    scope: NotBlankStr
    key: NotBlankStr
    status: IdempotencyOutcome
    response_hash: str | None = None
    response_body: str | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_response_columns_match_status(self) -> Self:
        """Enforce response-column invariant tied to ``status``.

        ``response_hash`` and ``response_body`` are both present iff
        ``status`` is :data:`IdempotencyOutcome.COMPLETED`. Rejects
        rows that would let a caller observe a half-written success
        (e.g. body without hash, or hash on a row whose status is
        still in-flight). Catches both buggy writes and corrupt rows
        loaded from disk.

        ``FRESH`` is a transient claim discriminator returned by
        :meth:`IdempotencyRepository.claim`; it MUST NOT appear on a
        persisted row -- the table only stores ``in_flight`` /
        ``completed`` / ``failed``. Reject it explicitly so a corrupt
        row (or a buggy writer) cannot smuggle FRESH past the model.
        """
        if self.status is IdempotencyOutcome.FRESH:
            msg = (
                "status FRESH is not valid for a persisted "
                "IdempotencyRecord (only in_flight/completed/failed)"
            )
            raise ValueError(msg)
        if self.status is IdempotencyOutcome.COMPLETED:
            if self.response_hash is None or self.response_body is None:
                msg = (
                    "response_hash and response_body must both be set "
                    "when status is COMPLETED"
                )
                raise ValueError(msg)
        elif self.response_hash is not None or self.response_body is not None:
            msg = (
                "response_hash and response_body must both be None when "
                f"status is {self.status.value!r}"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class IdempotencyRepository(Protocol):
    """Atomic claim-and-cache primitive for retry-safe endpoints."""

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: AwareDatetime,
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
        claim_token: NotBlankStr,
    ) -> bool:
        """Mark a claimed key as ``COMPLETED`` and store the response.

        Returns ``True`` if the row's stored ``claim_token`` matched
        and the write landed; ``False`` if the token has rotated
        (another worker re-claimed the key) or the row vanished. A
        ``False`` return signals a stale worker -- callers must not
        attempt to recover by ignoring it; the row already belongs to
        a different lease.
        """
        ...

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        claim_token: NotBlankStr,
    ) -> bool:
        """Mark a claimed key as ``FAILED`` so future retries can re-claim.

        Returns ``True`` only if the row's stored ``claim_token``
        matched -- a stale worker whose lease has rotated cannot
        flip the row to FAILED.
        """
        ...

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        """Fetch the persisted record verbatim (None if absent)."""
        ...

    async def cleanup_expired(self, now: AwareDatetime) -> int:
        """Delete expired rows. Returns the number of rows removed."""
        ...
