"""Shared configuration utilities."""

import copy
from collections.abc import Mapping
from typing import NoReturn

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_CONVERSION_ERROR

logger = get_logger(__name__)


def _conversion_error(
    value: object,
    field_name: str,
    *,
    cause: BaseException | None = None,
) -> NoReturn:
    """Log a numeric-conversion failure and raise ``ValueError``.

    Centralised so every rejection path in :func:`to_float` logs and
    chains identically.  The log records only the value's type, so a
    secret-bearing ``repr`` never reaches the sink; the raised
    ``ValueError`` keeps the ``repr`` for developer diagnostics.

    Raises:
        ValueError: Always, naming *field_name* and the offending value,
            chained from *cause* when one is supplied.
    """
    logger.warning(
        CONFIG_CONVERSION_ERROR,
        field=field_name,
        error="invalid numeric value",
        value_type=type(value).__name__,
    )
    msg = f"Invalid numeric value for {field_name}: {value!r}"
    if cause is not None:
        raise ValueError(msg) from cause
    raise ValueError(msg)


def to_float(value: object, *, field_name: str = "value") -> float:
    """Coerce a value to float with clear error reporting.

    Args:
        value: Value to convert.  Accepts ``str``, ``int``, and ``float``.
            ``bool`` is rejected: a YAML boolean landing in a numeric field
            is almost always a mistake, matching the rate-limit validators.
        field_name: Field name for error messages.

    Returns:
        Float value.

    Raises:
        ValueError: If *value* cannot be converted to float.
    """
    if value is None:
        msg = f"Expected numeric value for {field_name}, got None"
        logger.warning(CONFIG_CONVERSION_ERROR, field=field_name, error=msg)
        raise ValueError(msg)
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            _conversion_error(value, field_name, cause=exc)
    _conversion_error(value, field_name)


def deep_merge(
    base: Mapping[str, object],
    override: Mapping[str, object],
) -> dict[str, object]:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged recursively.  Lists, scalars, and all other
    types in *override* replace the corresponding value in *base*
    entirely.  Keys present only in *base* are preserved unchanged in
    the result.  Neither input dict is mutated.

    Args:
        base: Base configuration dict.
        override: Override values to layer on top.

    Returns:
        A new merged dict.
    """
    result: dict[str, object] = {
        key: copy.deepcopy(value) for key, value in base.items()
    }
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result
