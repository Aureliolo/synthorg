# module-kind: code
"""Classify an ollama plan-quota block apart from a transient rate limit.

ollama signals a spent plan allowance via a 429 (surfaced by LiteLLM as a
``RateLimitError``) whose body names the limit. There is no quota API to
pre-check remaining allowance (tracking: ollama/ollama#12532; the quota-API
requests ollama/ollama#15663 and #16448 are also open), so the caller runs
until ollama blocks and the block is classified here by its message.

The signatures are deliberately conservative: only phrases that mean the
allowance is exhausted, never a generic transient ``rate limit exceeded`` --
so a recoverable throttle stays retryable while a depleted plan does not.
"""

from typing import Final

_QUOTA_EXHAUSTION_SIGNATURES: Final[tuple[str, ...]] = (
    "quota",
    "usage limit",
    "weekly limit",
    "session limit",
    "plan limit",
    "upgrade your plan",
    "exceeded your",
)


def is_quota_exhaustion(exc: Exception) -> bool:
    """Return True when a rate-limit error reads as a spent plan allowance.

    Returns:
        True when the message matches a quota-exhaustion signature, so the
        caller surfaces it as non-retryable rather than retrying a depleted
        allowance that cannot recover within the window.
    """
    text = str(exc).lower()
    return any(signature in text for signature in _QUOTA_EXHAUSTION_SIGNATURES)
