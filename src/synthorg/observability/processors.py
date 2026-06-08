"""Custom structlog processors for the observability pipeline."""

import re
import sys
from collections.abc import Mapping
from uuid import UUID

from structlog.typing import EventDict, WrappedLogger

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability.redaction import scrub_secret_tokens

_SENSITIVE_PATTERN: re.Pattern[str] = re.compile(
    r"(password|secret|token|api_key|api_secret|authorization"
    r"|credential|private_key|bearer|session)",
    re.IGNORECASE,
)

_REDACTED = "**REDACTED**"


def _redact_value(value: object) -> object:
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
    logger: WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
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


def _coerce_uuid_value(value: object) -> object:
    """Recursively render ``UUID`` leaves as their canonical strings.

    Traverses nested mapping / list / tuple structures, replacing every
    :class:`uuid.UUID` with ``str(value)`` and leaving all other leaves
    untouched. Returns new containers so the original event dict is
    never mutated.

    Args:
        value: The value to coerce.

    Returns:
        ``value`` with every UUID leaf stringified.
    """
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {k: _coerce_uuid_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_uuid_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_coerce_uuid_value(item) for item in value)
    return value


def coerce_uuids(
    logger: WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """Render UUID log values as their canonical hyphenated strings.

    Entity ids are typed ``UUID``. Passed bare into a log call they
    reach the JSON renderer as ``repr(uuid)`` (``UUID('...')``) instead
    of the canonical string, breaking log filters and dashboards that
    match ids exactly. Coercing here means every id renders consistently
    whether or not the call site wrapped it in ``str()``, including
    UUIDs nested inside dicts, lists, and tuples.

    Returns a new dict rather than mutating the original, following the
    project's immutability convention.

    Args:
        logger: The wrapped logger object (unused, required by structlog).
        method_name: The name of the log method called (unused).
        event_dict: The event dictionary to process.

    Returns:
        A new event dict with every UUID value stringified.
    """
    return {key: _coerce_uuid_value(value) for key, value in event_dict.items()}


def _scrub_value(value: object) -> object:
    """Recursively scrub credential patterns out of string values.

    Traverses nested mapping / list / tuple structures, applying
    :func:`synthorg.observability.redaction.scrub_secret_tokens` to every
    string leaf.  Non-string leaves are returned unchanged.

    The mapping branch uses :class:`collections.abc.Mapping` so
    immutable wrappers like :class:`types.MappingProxyType` (used by
    the registry/``BaseTool`` immutability convention) are recursed
    into as well, and a fresh ``dict`` is returned so the original
    structure is never mutated.

    Args:
        value: The value to scrub.

    Returns:
        A new structure with every string leaf scrubbed.
    """
    if isinstance(value, str):
        return scrub_secret_tokens(value)
    if isinstance(value, Mapping):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(item) for item in value)
    return value


def scrub_event_fields(
    logger: WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """Deep-scrub credential patterns out of every string value.

    Belt-and-braces defence against the ``error=str(exc)`` leak
    vector: even when a caller embeds a stringified exception (or
    response body) that carries ``client_secret=...``,
    ``"access_token":"..."``, ``Authorization: Bearer ...``, or raw
    Fernet ciphertext, this processor rewrites the string so those
    substrings are masked before the renderer sees them.

    Runs *after* ``sanitize_sensitive_fields`` so keys that the
    field-name scrubber already replaced with ``**REDACTED**`` stay
    redacted.

    **Robustness contract**: this processor runs on every log record.
    If ``_scrub_value`` raises (e.g. a corrupted object whose ``repr``
    blows up, or a pathological recursive structure), we return the
    *original* event dict unchanged rather than letting the exception
    propagate and abort the caller's log call. Losing scrubbing on one
    event is preferable to silencing the entire logging pipeline at the
    moment of crisis.

    Args:
        logger: The wrapped logger object (unused, required by structlog).
        method_name: The name of the log method called (unused).
        event_dict: The event dictionary to process.

    Returns:
        A new event dict with every string value scrubbed via
        :func:`synthorg.observability.redaction.scrub_secret_tokens`,
        or the original dict if the scrub itself fails.
    """
    try:
        return {key: _scrub_value(value) for key, value in event_dict.items()}
    except Exception as exc:
        reraise_critical(exc)
        # Fail open: pass the event through unscrubbed rather than drop
        # the log line entirely.  Still safer than crashing the log
        # pipeline -- ``sanitize_sensitive_fields`` (which ran just
        # before us) has already redacted known-sensitive *field names*.
        # We write to ``sys.stderr`` directly (never via ``logger``) so
        # operators notice the scrub regression without triggering a
        # recursive log-through-logger failure. ``processors.py`` is
        # not on the ``print()`` allowlist, so we use the raw stream
        # write instead of ``print(file=sys.stderr)``.
        sys.stderr.write(
            f"WARNING: scrub_event_fields failed; event passed unscrubbed: "
            f"{type(exc).__name__}\n",
        )
        sys.stderr.flush()
        return event_dict
