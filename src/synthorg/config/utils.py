"""Shared configuration utilities."""

import copy
from typing import Any

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_CONVERSION_ERROR

logger = get_logger(__name__)


def to_float(value: Any, *, field_name: str = "value") -> float:
    """Coerce a value to float with clear error reporting.

    Args:
        value: Value to convert (str, int, float, etc.).
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
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        msg = f"Invalid numeric value for {field_name}: {value!r}"
        logger.warning(CONFIG_CONVERSION_ERROR, field=field_name, error=msg)
        raise ValueError(msg) from exc


def deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
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
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
