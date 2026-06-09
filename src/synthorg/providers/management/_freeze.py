# module-kind: code
"""Immutability + UTC helpers for the provider-capability audit DTOs.

Extracted from ``capability_dtos.py``: the recursive freeze/thaw pair
that keeps audit payloads deterministic on disk, plus the UTC-offset
guard used by the ``UTCDatetime`` annotated type.
"""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime


def _require_utc(value: datetime) -> datetime:
    """Reject ``AwareDatetime`` values whose offset is not exactly UTC.

    ``AwareDatetime`` accepts any non-naive offset (``+02:00``,
    ``-07:00``, etc.), but every persisted timestamp on this surface
    is documented and stored as UTC.  Enforcing the invariant at the
    DTO boundary keeps round-trips deterministic and pushes the
    burden of normalisation off downstream layers.

    Args:
        value: The aware datetime to validate.

    Returns:
        The unchanged *value* when its UTC offset is exactly zero.

    Raises:
        ValueError: If the datetime's offset is not exactly UTC.
    """
    if value.utcoffset() != UTC.utcoffset(None):
        msg = f"datetime must be in UTC; got offset {value.utcoffset()!r}"
        raise ValueError(msg)
    return value


UTCDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


def _recursively_freeze(value: object) -> object:
    """Return an immutable equivalent of ``value`` for audit-payload safety.

    Walks the structure and produces ``MappingProxyType`` for dicts and
    ``tuple`` for lists/tuples; scalars pass through unchanged.
    ``MappingProxyType`` instances re-enter the recursion so nested
    already-frozen mappings are normalised against the outer wrap.

    Sets and frozensets are explicitly rejected: an audit row needs a
    deterministic on-disk JSON shape so callers can diff and replay it,
    and Python's set iteration order is not stable across runs.  Senders
    that need set semantics in the payload should pass a sorted tuple
    instead.

    Args:
        value: The value to deep-freeze.

    Returns:
        An immutable equivalent of *value* (``MappingProxyType`` for
        dicts, ``tuple`` for lists/tuples; scalars unchanged).

    Raises:
        TypeError: If *value* is a ``set`` or ``frozenset`` (forbidden
            for determinism).
    """
    if isinstance(value, (set, frozenset)):
        msg = (
            f"audit payload disallows {type(value).__name__} for determinism; "
            "use a sorted tuple instead"
        )
        raise TypeError(msg)
    if isinstance(value, MappingProxyType):
        # Re-freeze recursively so nested values inserted prior to
        # wrapping still get the same treatment.
        return MappingProxyType(
            {k: _recursively_freeze(v) for k, v in value.items()},
        )
    if isinstance(value, dict):
        return MappingProxyType(
            {k: _recursively_freeze(v) for k, v in value.items()},
        )
    if isinstance(value, (list, tuple)):
        return tuple(_recursively_freeze(item) for item in value)
    return value


def _recursively_thaw(value: object) -> object:
    """Inverse of :func:`_recursively_freeze` for JSON serialisation.

    Pydantic-core cannot encode ``MappingProxyType`` directly, so each
    immutable container is thawed back to ``dict`` / ``list``. Sets are
    rejected here too so a post-construction ``_freeze_payload`` bypass
    fails fast instead of emitting a non-deterministic audit row.

    Args:
        value: The value to thaw.

    Returns:
        A JSON-serialisable copy of *value* with ``MappingProxyType``
        replaced by ``dict`` and tuples replaced by ``list``.

    Raises:
        TypeError: If *value* is a ``set`` or ``frozenset`` (forbidden
            for determinism).
    """
    if isinstance(value, (set, frozenset)):
        msg = (
            f"audit payload disallows {type(value).__name__} for determinism; "
            "use a sorted tuple instead"
        )
        raise TypeError(msg)
    if isinstance(value, MappingProxyType):
        return {k: _recursively_thaw(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: _recursively_thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_recursively_thaw(item) for item in value]
    return value
