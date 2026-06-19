# module-kind: code
"""Shared field validators for the provider request DTOs.

The request models, response models, and mappers each live in their
own sibling module; these validators are shared across the request DTOs.
"""

import re
from urllib.parse import urlparse

from pydantic import SecretStr

from synthorg.observability import safe_error_description

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_RESERVED_PROVIDER_NAMES: frozenset[str] = frozenset(
    {"presets", "from-preset", "probe-local", "discovery-policy"},
)
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")


def _validate_provider_name(v: str) -> str:
    """Validate a provider name against naming rules.

    Args:
        v: Candidate provider name.

    Returns:
        The validated name.

    Raises:
        ValueError: If the name is invalid or reserved.
    """
    if not _PROVIDER_NAME_PATTERN.match(v):
        msg = (
            "Provider name must be 2-64 chars, lowercase "
            "alphanumeric and hyphens, starting/ending with "
            "alphanumeric"
        )
        raise ValueError(msg)
    if v in _RESERVED_PROVIDER_NAMES:
        msg = f"Provider name {v!r} is reserved"
        raise ValueError(msg)
    return v


def _validate_http_url(v: str | None, *, field: str) -> str | None:
    """Validate that ``v`` is an http/https URL with a host, or ``None``.

    Beyond the scheme check, requires ``parsed.hostname`` to be present
    (rejects host-less inputs like ``http:///path``) and force-resolves
    ``parsed.port`` so malformed ports like ``https://api.example.com:bad``
    raise here instead of surfacing as a generic socket error at use
    time.  ``urlparse(...).port`` raises ``ValueError`` lazily on bad
    input, so accessing the property is the canonical pre-flight check.

    Args:
        v: Candidate URL or ``None``.
        field: Field name for error messages.

    Returns:
        The unchanged URL string when valid, or ``None`` when *v* is
        ``None``.

    Raises:
        ValueError: If the URL lacks an http/https scheme or host, or
            has a malformed port.
    """
    if v is None:
        return v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        msg = f"{field} must use http or https scheme, got {parsed.scheme!r}"
        raise ValueError(msg)
    if not parsed.hostname:
        msg = f"{field} must include a host"
        raise ValueError(msg)
    try:
        _ = parsed.port  # raises ValueError on a malformed ``host:bad`` port
    except ValueError as exc:
        msg = f"{field} has malformed port: {safe_error_description(exc)}"
        raise ValueError(msg) from exc
    return v


def _validate_base_url(v: str | None) -> str | None:
    """Validate that a base URL uses http or https scheme.

    Args:
        v: Candidate base URL or ``None``.

    Returns:
        The validated base URL string, or ``None`` when *v* is ``None``.
    """
    return _validate_http_url(v, field="base_url")


def _validate_oauth_token_url(v: str | None) -> str | None:
    """Validate that an OAuth token URL uses http or https scheme.

    Args:
        v: Candidate OAuth token URL or ``None``.

    Returns:
        The validated OAuth token URL string, or ``None`` when *v* is
        ``None``.
    """
    return _validate_http_url(v, field="oauth_token_url")


def _reject_blank_secret(v: SecretStr | None, *, field: str) -> SecretStr | None:
    """Reject ``SecretStr`` whose unwrapped value is empty / whitespace.

    ``SecretStr("")`` is truthy as an object reference, so ``is not
    None`` checks downstream cannot distinguish "secret missing" from
    "secret was blanked out".  Catching the empty-string case at the
    DTO boundary keeps callers from having to ``get_secret_value()``
    just to test presence.  ``None`` (the explicit "not provided" /
    "do not change" signal) is allowed.

    Args:
        v: Candidate secret or ``None``.
        field: Field name for error messages.

    Returns:
        The unchanged ``SecretStr`` when non-empty, or ``None`` when *v*
        is ``None``.

    Raises:
        ValueError: If the unwrapped secret is empty or whitespace-only.
    """
    if v is None:
        return v
    if not v.get_secret_value().strip():
        msg = f"{field} must be a non-empty value if provided"
        raise ValueError(msg)
    return v
