"""Shared HTTP helpers for the per-forge REST clients.

Centralises status-code -> typed-error mapping, rate-limit header
parsing, and response-body sanitisation so each per-forge client stays
terse and every failure surfaces as a typed
:class:`~synthorg.engine.errors.GitBackendError` subclass. Tokens
travel in the ``Authorization`` header only and are never logged; the
sanitiser strips token-like patterns from any body snippet.
"""

import re
from typing import Final

import httpx

from synthorg.engine.errors import (
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendRateLimitError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_RATE_LIMITED,
    FORGE_API_REQUEST_FAILED,
)

logger = get_logger(__name__)

_AUTH_STATUS: Final[frozenset[int]] = frozenset({401, 403})
_RATE_LIMIT_STATUS: Final[int] = 429
# Only GitHub's *primary* rate limit surfaces as 403 + a zeroed
# remaining header; a 401 is always genuine auth failure, never a
# disguised rate limit, so the remaining-header heuristic is scoped to
# 403 to keep auth failures non-retryable.
_PRIMARY_RATE_LIMIT_STATUS: Final[int] = 403
_RATE_LIMIT_REMAINING_HEADER: Final[str] = "x-ratelimit-remaining"
_RETRY_AFTER_HEADER: Final[str] = "retry-after"
_MAX_BODY_SNIPPET: Final[int] = 500

_TOKEN_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"Bearer\s+[^\s\"]+|"
    r"token\s+[^\s\"]+|"
    r"ghp_[A-Za-z0-9]+|"
    r"gho_[A-Za-z0-9]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"glpat-[A-Za-z0-9_-]+|"
    r"Authorization:\s*[^\n]+",
    re.IGNORECASE,
)


def sanitize_body(text: str) -> str:
    """Strip token-like material from a response body before logging.

    Returns:
        The first ``_MAX_BODY_SNIPPET`` characters of ``text`` with
        token / Authorization patterns replaced by ``[REDACTED]``.
    """
    return _TOKEN_PATTERNS.sub("[REDACTED]", text[:_MAX_BODY_SNIPPET])


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) if present.

    Returns:
        The ``Retry-After`` delay in seconds when present and
        parseable; ``None`` for absent / non-numeric headers (HTTP-
        date form is intentionally not parsed).
    """
    raw = headers.get(_RETRY_AFTER_HEADER)
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        # HTTP-date form is valid per spec but rare for forges; the
        # exponential backoff handles the wait when we cannot parse a
        # delta-seconds value.
        return None
    return value if value >= 0 else None


def _is_rate_limited(resp: httpx.Response) -> bool:
    """Detect rate limiting across forge conventions.

    GitHub returns 403 with ``X-RateLimit-Remaining: 0`` for primary
    limits and 429 for secondary limits; GitLab/Gitea use 429.

    Returns:
        ``True`` when the response shape indicates a rate-limit hit
        for any supported forge; ``False`` otherwise.
    """
    if resp.status_code == _RATE_LIMIT_STATUS:
        return True
    if resp.status_code == _PRIMARY_RATE_LIMIT_STATUS:
        remaining = resp.headers.get(_RATE_LIMIT_REMAINING_HEADER)
        return remaining is not None and remaining.strip() == "0"
    return False


def raise_for_forge_status(resp: httpx.Response, *, action: str) -> None:
    """Raise the matching typed error on a non-2xx forge response.

    Args:
        resp: The forge HTTP response.
        action: Human-readable action for the error message.

    Raises:
        GitBackendRateLimitError: 429 / primary-limit 403.
        GitBackendForgeAuthError: 401/403 (non-rate-limit).
        GitBackendForgeApiError: Any other non-2xx status.
    """
    if resp.is_success:
        return
    body = sanitize_body(resp.text) if resp.text else "(empty)"
    if _is_rate_limited(resp):
        retry_after = parse_retry_after(resp.headers)
        logger.warning(
            FORGE_API_RATE_LIMITED,
            action=action,
            status_code=resp.status_code,
            retry_after=retry_after,
        )
        msg = f"forge rate-limited while attempting to {action}"
        raise GitBackendRateLimitError(msg, retry_after=retry_after)
    logger.warning(
        FORGE_API_REQUEST_FAILED,
        action=action,
        status_code=resp.status_code,
        response_body=body,
    )
    if resp.status_code in _AUTH_STATUS:
        msg = f"forge authentication failed while attempting to {action}"
        raise GitBackendForgeAuthError(msg)
    msg = f"forge API failed to {action} (status {resp.status_code})"
    raise GitBackendForgeApiError(msg)


__all__ = [
    "parse_retry_after",
    "raise_for_forge_status",
    "sanitize_body",
]
