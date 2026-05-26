"""Sliding-window rate limiter Protocol.

The ``SlidingWindowStore`` Protocol is the pluggable contract that
per-operation guards call to acquire a slot.  The default implementation
lives in ``in_memory.py``; alternative adapters can be added behind the
factory without touching guard logic.

The Protocol is intentionally minimal -- one method, one dataclass-like
outcome -- so adapters can be added without touching guard logic.
"""

from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RateLimitOutcome(BaseModel):
    """Result of a single ``acquire`` call.

    Attributes:
        allowed: ``True`` when the request may proceed, ``False`` when
            it should be rejected with HTTP 429.
        retry_after_seconds: Suggested wait before retrying.  ``None``
            when ``allowed`` is ``True``.
        remaining: Approximate remaining slots in the current window.
            Callers must supply a non-negative value; negative values
            are rejected by the ``ge=0`` validator at construction time.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allowed: bool
    retry_after_seconds: float | None = Field(default=None, ge=0.0)
    remaining: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_retry_after_allowed(self) -> Self:
        """``retry_after_seconds`` only makes sense when ``allowed`` is False.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.allowed and self.retry_after_seconds is not None:
            msg = "retry_after_seconds must be None when allowed is True"
            raise ValueError(msg)
        if not self.allowed and self.retry_after_seconds is None:
            msg = "retry_after_seconds must be set when allowed is False"
            raise ValueError(msg)
        return self


@runtime_checkable
class SlidingWindowStore(Protocol):
    """Async sliding-window rate limiter.

    Implementations must be safe to call concurrently from the same
    event loop.  A single logical bucket is identified by the ``key``
    argument; the guard constructs keys of the form
    ``"{operation}:{user_or_ip}"`` so buckets never leak across
    operations or subjects.
    """

    async def acquire(
        self,
        key: str,
        *,
        max_requests: int,
        window_seconds: int,
    ) -> RateLimitOutcome:
        """Record one request against ``key`` and return the outcome.

        Args:
            key: Bucket identifier.  Stable per (operation, subject).
            max_requests: Maximum requests allowed per window.  Must
                be positive.
            window_seconds: Rolling window length in seconds.  Must be
                positive.

        Returns:
            :class:`RateLimitOutcome` indicating whether the caller may
            proceed, plus ``retry_after_seconds`` when rejected.
        """
        ...

    async def close(self) -> None:
        """Release any background resources (connections, timers)."""
        ...
