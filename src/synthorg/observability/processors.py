"""Custom structlog processors for the observability pipeline."""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

_SENSITIVE_PATTERN: re.Pattern[str] = re.compile(
    r"(password|secret|token|api_key|api_secret|authorization"
    r"|credential|private_key|bearer|session)",
    re.IGNORECASE,
)

_REDACTED = "**REDACTED**"


def _redact_value(value: Any) -> Any:
    """Recursively redact sensitive keys in nested structures.

    Args:
        value: The value to inspect and potentially redact.

    Returns:
        A new structure with sensitive keys redacted at all depths.
    """
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _SENSITIVE_PATTERN.search(k)
                else _redact_value(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def sanitize_sensitive_fields(
    logger: Any,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """Redact values of keys matching sensitive patterns.

    Returns a new dict rather than mutating the original event dict,
    following the project's immutability convention.  Redaction is
    applied recursively to nested dicts, lists, and tuples.

    Args:
        logger: The wrapped logger object (unused, required by structlog).
        method_name: The name of the log method called (unused).
        event_dict: The event dictionary to process.

    Returns:
        A new event dict with sensitive values replaced by
        ``**REDACTED**`` at all nesting depths.
    """
    return {
        key: (
            _REDACTED
            if isinstance(key, str) and _SENSITIVE_PATTERN.search(key)
            else _redact_value(value)
        )
        for key, value in event_dict.items()
    }
