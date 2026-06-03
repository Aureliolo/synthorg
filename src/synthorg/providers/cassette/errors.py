"""Cassette error hierarchy.

All cassette errors subclass :class:`ProviderError` so they flow
through the provider-layer error handling and metrics already in
place, while staying non-retryable: a cassette miss is a deterministic
misconfiguration, never something a retry can fix. They surface loudly
rather than silently falling through to a real provider, which would
break the "zero real LLM calls" guarantee.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)


class CassetteError(ProviderError):
    """Base for every cassette record/replay error."""

    is_retryable = False
    default_message: ClassVar[str] = "Cassette error"


class CassetteReplayMissError(CassetteError):
    """Replay requested a response for a request not in the cassette.

    Raised instead of calling a real provider so a replay run can
    never silently incur LLM spend or non-determinism.
    """

    default_message: ClassVar[str] = (
        "No recorded cassette interaction matches this request"
    )


class CassetteReplayExhaustedError(CassetteError):
    """A request matched a recorded key but its FIFO sequence is spent.

    The run issued the same request more times than it was recorded
    making it; the extra call cannot be served deterministically.
    """

    default_message: ClassVar[str] = (
        "Recorded cassette interactions exhausted for this request"
    )


class CassetteFormatError(CassetteError):
    """The cassette file is malformed or version-incompatible."""

    default_message: ClassVar[str] = (
        "Cassette file is malformed or version-incompatible"
    )


class CassetteIntegrityError(CassetteError):
    """The cassette body does not match its recorded integrity digest.

    The document carries a sha256 over its canonical interactions payload;
    a load that recomputes a different digest (or finds the header absent)
    means the file was edited or corrupted after recording, so replaying it
    would silently serve tampered responses. Refused loudly instead.
    """

    default_message: ClassVar[str] = (
        "Cassette body does not match its recorded integrity digest"
    )


class CassetteInternalError(CassetteError):
    """A cassette wrapper invariant was violated.

    Used by the unreachable ``_do_*`` guards on the wrapper: every
    public method is overridden and never delegates into the base
    resilience path, so these hooks must never execute.
    """

    default_message: ClassVar[str] = "Cassette wrapper internal invariant violated"


# Recorded-error replay: map a recorded provider-error class name back
# to its concrete type so error-driven control flow re-executes
# identically. Unknown names degrade to the generic ``ProviderError``
# (still a faithful "the provider raised" signal).
_REPLAYABLE_PROVIDER_ERRORS: tuple[type[ProviderError], ...] = (
    AuthenticationError,
    RateLimitError,
    ModelNotFoundError,
    InvalidRequestError,
    ContentFilterError,
    ProviderTimeoutError,
    ProviderConnectionError,
    ProviderInternalError,
    ProviderError,
)
_PROVIDER_ERROR_BY_NAME: dict[str, type[ProviderError]] = {
    cls.__name__: cls for cls in _REPLAYABLE_PROVIDER_ERRORS
}


def provider_error_for(
    error_class: str,
    message: str,
    *,
    context: dict[str, JsonValue] | None = None,
) -> ProviderError:
    """Reconstruct a provider error to re-raise on replay.

    Args:
        error_class: Recorded ``type(exc).__name__``.
        message: Recorded, already-scrubbed error description.
        context: Recorded (already-scrubbed) ``ProviderError.context``,
            rehydrated so callers that branch on it replay identically.

    Returns:
        An instance of the matching :class:`ProviderError` subclass, or
        a generic :class:`ProviderError` when the class is unknown.

    Note:
        A reconstructed :class:`RateLimitError` has ``retry_after=None``:
        the cassette records only ``error_class`` / ``message`` /
        ``context``, not the separate ``retry_after`` attribute. This is
        harmless because cassette replay never retries (cassette errors
        are non-retryable by design), so no caller consults the replayed
        ``retry_after``.
    """
    cls = _PROVIDER_ERROR_BY_NAME.get(error_class, ProviderError)
    merged: dict[str, JsonValue] = {
        **(context or {}),
        "cassette_replayed_error_class": error_class,
    }
    return cls(message, context=merged)


__all__ = [
    "CassetteError",
    "CassetteFormatError",
    "CassetteIntegrityError",
    "CassetteInternalError",
    "CassetteReplayExhaustedError",
    "CassetteReplayMissError",
    "provider_error_for",
]
