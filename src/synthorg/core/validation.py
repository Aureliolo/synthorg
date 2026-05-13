"""Shared validation utilities for domain value formats."""

_ACTION_TYPE_PARTS: int = 2


def is_valid_action_type(action_type: str) -> bool:
    """Check whether ``action_type`` follows ``category:action`` format.

    Args:
        action_type: The action type string to validate.

    Returns:
        ``True`` if the string has exactly one colon separating
        two non-blank segments, ``False`` otherwise.
    """
    parts = action_type.split(":")
    if len(parts) != _ACTION_TYPE_PARTS:
        return False
    return bool(parts[0].strip() and parts[1].strip())


def require_non_blank(value: object, *, name: str) -> str:
    """Return ``str(value)`` if non-blank, else raise ``ValueError``.

    ``None`` and empty / whitespace-only strings both fail. Generic
    helper for the "give me a non-blank string or fail loudly" pattern
    repeated across factories. Domain wrappers wrap the call to
    re-raise as their own ``<Domain><Condition>Error`` and to log
    domain-specific events; this helper is intentionally
    logging-free so callers retain full control of observability.

    Args:
        value: Any object (most commonly ``str | None``).
        name: Field or argument name surfaced in the error message.

    Returns:
        ``str(value)`` after non-blank validation.

    Raises:
        ValueError: If ``value`` is ``None`` or, after ``str()`` and
            ``.strip()``, is the empty string.
    """
    if value is None or not str(value).strip():
        msg = f"{name} must be a non-blank string, got {value!r}"
        raise ValueError(msg)
    return str(value)


def coerce_positive_int(
    value: object,
    *,
    name: str,
    default: int,
) -> int:
    """Coerce a config value to a positive ``int``, falling back to ``default``.

    Accepts ``int`` and string-encoded integers, rejects ``bool``
    (because ``isinstance(True, int)`` is true under Python's number
    tower and would silently turn a YAML ``true`` into 1).  Rejects
    non-positive results so misconfigured zero / negative values fail
    early instead of producing nonsensical limits downstream.

    Intentionally logging-free; callers wrap to add domain-specific
    log events (e.g. ``HR_TRAINING_CONFIG_INVALID``).

    Args:
        value: Raw config value.  ``None`` triggers the default.
        name: Field name surfaced in error messages.
        default: Value returned when ``value`` is ``None``.

    Returns:
        A positive ``int``.

    Raises:
        TypeError: If ``value`` is a ``bool`` or any non-``int``/-``str``.
        ValueError: If ``value`` is a string that does not parse to
            ``int``, or if the resulting integer is <= 0.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        msg = f"{name} must be a positive integer, got bool"
        raise TypeError(msg)
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, str):
        try:
            coerced = int(value)
        except ValueError as exc:
            msg = f"{name} must be a positive integer, got {value!r}"
            raise ValueError(msg) from exc
    else:
        msg = f"{name} must be a positive integer, got {type(value).__name__}"
        raise TypeError(msg)
    if coerced <= 0:
        msg = f"{name} must be > 0, got {coerced}"
        raise ValueError(msg)
    return coerced
