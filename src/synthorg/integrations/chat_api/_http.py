"""Shared HTTP helpers for the chat-platform Web API clients.

Centralises HTTP status -> typed-error mapping, ``Retry-After`` parsing,
and response-body sanitisation. Tokens travel in the ``Authorization``
header only and are never logged; the sanitiser strips token-like
patterns (Slack ``xox*`` tokens, bearer prefixes) from any body snippet.
"""

import re
from typing import Final

import httpx

from synthorg.core.resilience import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CHAT_API_RATE_LIMITED,
    CHAT_API_REQUEST_FAILED,
)

logger = get_logger(__name__)

_AUTH_STATUS: Final[frozenset[int]] = frozenset({401, 403})
_RATE_LIMIT_STATUS: Final[int] = 429
_RETRY_AFTER_HEADER: Final[str] = "retry-after"
_MAX_BODY_SNIPPET: Final[int] = 500

_TOKEN_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"Bearer\s+[^\s\"]+|xox[abpser]-[A-Za-z0-9-]+|Authorization:\s*[^\n]+",
    re.IGNORECASE,
)


def sanitize_body(text: str) -> str:
    """Strip token-like material from a response body before logging.

    Returns:
        The first ``_MAX_BODY_SNIPPET`` characters of ``text`` with
        token / Authorization patterns replaced by ``[REDACTED]``.
    """
    return _TOKEN_PATTERNS.sub("[REDACTED]", text[:_MAX_BODY_SNIPPET])


def _error_detail(resp: httpx.Response) -> str:
    """Extract just the chat error field for a log line.

    A raw body can echo request field values, so only the parsed
    ``error`` / ``message`` field is logged, sanitised. Falls back to a
    generic status marker when the body carries neither.

    Returns:
        The sanitised error detail, or a generic status marker.
    """
    try:
        payload = resp.json()
    except ValueError:
        return f"(non-JSON body, status {resp.status_code})"
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("message")
        if isinstance(detail, str) and detail:
            return sanitize_body(detail)
    return f"(no error field, status {resp.status_code})"


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Parse a ``Retry-After`` header to finite non-negative seconds.

    Returns:
        The delay in seconds when present and parseable; ``None``
        otherwise.
    """
    raw = headers.get(_RETRY_AFTER_HEADER)
    if raw is None:
        return None
    return coerce_finite_nonneg_seconds(parse_retry_after_seconds(raw.strip()))


def raise_for_chat_status(resp: httpx.Response, *, action: str) -> None:
    """Raise the matching typed error on a non-2xx chat response.

    Args:
        resp: The chat HTTP response.
        action: Human-readable action for the error message.

    Raises:
        ChatApiRateLimitError: On HTTP 429.
        ChatApiAuthError: On HTTP 401/403.
        ChatApiError: On any other non-2xx status.
    """
    if resp.is_success:
        return
    if resp.status_code == _RATE_LIMIT_STATUS:
        retry_after = parse_retry_after(resp.headers)
        logger.warning(
            CHAT_API_RATE_LIMITED,
            action=action,
            status_code=resp.status_code,
            retry_after=retry_after,
        )
        msg = f"chat platform rate-limited while attempting to {action}"
        raise ChatApiRateLimitError(msg, retry_after_seconds=retry_after)
    logger.warning(
        CHAT_API_REQUEST_FAILED,
        action=action,
        status_code=resp.status_code,
        detail=_error_detail(resp),
    )
    if resp.status_code in _AUTH_STATUS:
        msg = f"chat authentication failed while attempting to {action}"
        raise ChatApiAuthError(msg)
    msg = f"chat API failed to {action} (status {resp.status_code})"
    raise ChatApiError(msg)


__all__ = ["parse_retry_after", "raise_for_chat_status", "sanitize_body"]
