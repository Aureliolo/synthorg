# module-kind: code
"""Classify the two upstream billing conditions a retry cannot clear.

Both mean "an operator has to act", and both would otherwise be retried as
transient failures until the ladder gives up:

- **A spent plan allowance** arrives as a 429, indistinguishable at the
  status line from an ordinary throttle, so it is recognised from the
  message body. ollama exposes no quota API to pre-check remaining
  allowance (tracking: ollama/ollama#12532; the quota-API requests
  ollama/ollama#15663 and #16448 are also open), so the caller runs until
  ollama blocks and the block is classified here. The signatures are
  deliberately conservative: only phrases that mean the allowance is
  exhausted, never a generic ``rate limit exceeded``, so a recoverable
  throttle stays retryable while a depleted plan does not.
- **An empty extra-usage balance** arrives as a 402, which means exactly
  that and nothing else, so it is read off the status rather than guessed
  from wording. Reading the status keeps the check provider-neutral, and
  refusing to infer one from text keeps every error whose message happens
  to mention payment out of the bucket.
"""

from typing import Final

from synthorg.observability import safe_error_description

_QUOTA_EXHAUSTION_SIGNATURES: Final[tuple[str, ...]] = (
    "insufficient quota",
    "quota exceeded",
    "quota exhausted",
    "usage limit",
    "weekly limit",
    "session limit",
    "plan limit",
    "upgrade your plan",
    "exceeded your quota",
    "exceeded your usage limit",
    "exceeded your weekly limit",
    "exceeded your session limit",
    "exceeded your plan limit",
)

_PAYMENT_REQUIRED_STATUS: Final[int] = 402


def is_quota_exhaustion(exc: Exception) -> bool:
    """Return True when a rate-limit error reads as a spent plan allowance.

    Returns:
        True when the message matches a quota-exhaustion signature, so the
        caller surfaces it as non-retryable rather than retrying a depleted
        allowance that cannot recover within the window.
    """
    text = safe_error_description(exc).lower()
    return any(signature in text for signature in _QUOTA_EXHAUSTION_SIGNATURES)


def is_payment_required(exc: Exception) -> bool:
    """Return True when *exc* carries an upstream HTTP 402.

    ``bool`` is excluded explicitly because it is an ``int`` subclass, so a
    driver that stamped a flag onto the attribute would otherwise compare
    against a status code.

    Returns:
        True only when the exception reports status 402; an exception with
        no status, or a non-integer one, is never read as a billing
        condition.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return False
    return status == _PAYMENT_REQUIRED_STATUS


__all__ = ["is_payment_required", "is_quota_exhaustion"]
