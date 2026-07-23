"""Shared HTTP helpers for the container-registry API clients.

Centralises HTTP status to typed-error mapping, ``Retry-After`` parsing,
``WWW-Authenticate`` challenge parsing, and response-body sanitisation.
Credentials travel in the ``Authorization`` header only and are never logged;
the sanitiser strips token-like patterns from any body snippet.
"""

import re
from typing import Final

import httpx

from synthorg.core.resilience import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.integrations.errors import (
    RegistryApiAuthError,
    RegistryApiClientError,
    RegistryApiError,
    RegistryApiRateLimitError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    REGISTRY_API_RATE_LIMITED,
    REGISTRY_API_REQUEST_FAILED,
)

logger = get_logger(__name__)

_AUTH_STATUS: Final[frozenset[int]] = frozenset({401, 403})
_RATE_LIMIT_STATUS: Final[int] = 429
_CLIENT_ERROR_FLOOR: Final[int] = 400
_SERVER_ERROR_FLOOR: Final[int] = 500
_RETRY_AFTER_HEADER: Final[str] = "retry-after"
_WWW_AUTHENTICATE_HEADER: Final[str] = "www-authenticate"
_MAX_BODY_SNIPPET: Final[int] = 500

_TOKEN_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"Bearer\s+[^\s\"]+|Authorization:\s*[^\n]+",
    re.IGNORECASE,
)
# ``key="value"`` (or bare token) pairs inside a ``WWW-Authenticate`` header.
_CHALLENGE_PARAM_RE: Final[re.Pattern[str]] = re.compile(
    r'(?P<key>[a-zA-Z0-9_-]+)="(?P<value>[^"]*)"'
)


def sanitize_body(text: str) -> str:
    """Strip token-like material from a response body before logging.

    Returns:
        The first ``_MAX_BODY_SNIPPET`` characters of ``text`` with token /
        Authorization patterns replaced by ``[REDACTED]``.
    """
    return _TOKEN_PATTERNS.sub("[REDACTED]", text[:_MAX_BODY_SNIPPET])


def _error_detail(resp: httpx.Response) -> str:
    """Extract just the registry error field for a log line.

    A raw body can echo request values, so only the parsed ``errors``
    field is logged, sanitised.

    Returns:
        The sanitised error detail, or a generic status marker.
    """
    try:
        payload = resp.json()
    except ValueError:
        return f"(non-JSON body, status {resp.status_code})"
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, str) and message:
                    return sanitize_body(message)
    return f"(no error field, status {resp.status_code})"


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Parse a ``Retry-After`` header to finite non-negative seconds.

    Returns:
        The delay in seconds when present and parseable; ``None`` otherwise.
    """
    raw = headers.get(_RETRY_AFTER_HEADER)
    if raw is None:
        return None
    return coerce_finite_nonneg_seconds(parse_retry_after_seconds(raw.strip()))


def parse_bearer_challenge(headers: httpx.Headers) -> dict[str, str] | None:
    """Parse a ``WWW-Authenticate: Bearer ...`` challenge into its params.

    Args:
        headers: The 401 response headers.

    Returns:
        The challenge parameters (``realm`` / ``service`` / ``scope`` / ...)
        when the header advertises a ``Bearer`` scheme, else ``None`` (no
        header, or a ``Basic``-only challenge that needs no token exchange).
    """
    raw = headers.get(_WWW_AUTHENTICATE_HEADER)
    if raw is None or not raw.lstrip().lower().startswith("bearer"):
        return None
    return {
        m.group("key").lower(): m.group("value")
        for m in _CHALLENGE_PARAM_RE.finditer(raw)
    }


def raise_for_registry_status(resp: httpx.Response, *, action: str) -> None:
    """Raise the matching typed error on a non-2xx registry response.

    Args:
        resp: The registry HTTP response.
        action: Human-readable action for the error message.

    Raises:
        RegistryApiRateLimitError: On HTTP 429.
        RegistryApiAuthError: On HTTP 401/403.
        RegistryApiClientError: On any other deterministic 4xx (non-retryable).
        RegistryApiError: On a 5xx / other transient status (retryable).
    """
    if resp.is_success:
        return
    if resp.status_code == _RATE_LIMIT_STATUS:
        retry_after = parse_retry_after(resp.headers)
        logger.warning(
            REGISTRY_API_RATE_LIMITED,
            action=action,
            status_code=resp.status_code,
            retry_after=retry_after,
        )
        msg = f"registry rate-limited while attempting to {action}"
        raise RegistryApiRateLimitError(msg, retry_after_seconds=retry_after)
    logger.warning(
        REGISTRY_API_REQUEST_FAILED,
        action=action,
        status_code=resp.status_code,
        detail=_error_detail(resp),
    )
    if resp.status_code in _AUTH_STATUS:
        msg = f"registry authentication failed while attempting to {action}"
        raise RegistryApiAuthError(msg)
    if _CLIENT_ERROR_FLOOR <= resp.status_code < _SERVER_ERROR_FLOOR:
        msg = f"registry rejected the request to {action} (status {resp.status_code})"
        raise RegistryApiClientError(msg)
    msg = f"registry failed to {action} (status {resp.status_code})"
    raise RegistryApiError(msg)


__all__ = [
    "parse_bearer_challenge",
    "parse_retry_after",
    "raise_for_registry_status",
    "sanitize_body",
]
