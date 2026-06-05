"""Config resolution and validation for the throughput-adaptive strategy.

Holds the sprint-level config constants plus two families of helpers:
the lenient ``resolve_*`` functions used at runtime (fall back to the
default with a warning) and the strict ``validate_*`` functions used by
``validate_strategy_config`` (raise on a malformed value).
"""

import math
from typing import TYPE_CHECKING, Any

from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_STRATEGY_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

_KEY_VELOCITY_DROP_THRESHOLD_PCT: str = "velocity_drop_threshold_pct"
_KEY_VELOCITY_SPIKE_THRESHOLD_PCT: str = "velocity_spike_threshold_pct"
_KEY_MEASUREMENT_WINDOW_TASKS: str = "measurement_window_tasks"

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        _KEY_VELOCITY_DROP_THRESHOLD_PCT,
        _KEY_VELOCITY_SPIKE_THRESHOLD_PCT,
        _KEY_MEASUREMENT_WINDOW_TASKS,
    }
)

_DEFAULT_DROP_THRESHOLD_PCT: float = 30.0
_DEFAULT_SPIKE_THRESHOLD_PCT: float = 50.0
_DEFAULT_WINDOW_SIZE: int = 10
_MIN_WINDOW_SIZE: int = 2
_MAX_WINDOW_SIZE: int = 100
_MIN_THRESHOLD_PCT: float = 1.0
_MAX_THRESHOLD_PCT: float = 100.0
_DEFAULT_TRANSITION_THRESHOLD: float = 1.0


def resolve_bool(
    config: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    """Resolve a boolean config value with lenient fallback.

    Returns:
        The configured bool when present and well-typed; otherwise
        ``default`` (with a warning log).
    """
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    logger.warning(
        SPRINT_CEREMONY_SKIPPED,
        reason="invalid_bool_config",
        key=key,
        value=value,
        fallback=default,
        strategy="throughput_adaptive",
    )
    return default


def resolve_threshold(
    config: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    """Resolve a percentage threshold with lenient validation.

    Returns:
        The configured threshold when present, numeric, finite,
        and inside ``[_MIN_THRESHOLD_PCT, _MAX_THRESHOLD_PCT]``;
        otherwise ``default`` (with a warning log).
    """
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            reason="invalid_threshold",
            key=key,
            value=value,
            fallback=default,
            strategy="throughput_adaptive",
        )
        return default
    if not math.isfinite(value) or not (
        _MIN_THRESHOLD_PCT <= value <= _MAX_THRESHOLD_PCT
    ):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            reason="threshold_out_of_range",
            key=key,
            value=value,
            fallback=default,
            strategy="throughput_adaptive",
        )
        return default
    result: float = float(value)
    return result


def resolve_window_size(config: Mapping[str, Any]) -> int:
    """Resolve the measurement window size with lenient validation.

    Returns:
        The configured window size when present, an integer, and
        within ``[_MIN_WINDOW_SIZE, _MAX_WINDOW_SIZE]``; otherwise
        :data:`_DEFAULT_WINDOW_SIZE` (with a warning log).
    """
    value = config.get(_KEY_MEASUREMENT_WINDOW_TASKS)
    if value is None:
        return _DEFAULT_WINDOW_SIZE
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            reason="invalid_window_size",
            value=value,
            fallback=_DEFAULT_WINDOW_SIZE,
            strategy="throughput_adaptive",
        )
        return _DEFAULT_WINDOW_SIZE
    if not (_MIN_WINDOW_SIZE <= value <= _MAX_WINDOW_SIZE):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            reason="window_size_out_of_range",
            value=value,
            fallback=_DEFAULT_WINDOW_SIZE,
            strategy="throughput_adaptive",
        )
        return _DEFAULT_WINDOW_SIZE
    result: int = value
    return result


def validate_threshold_key(
    config: Mapping[str, Any],
    key: str,
) -> None:
    """Validate a percentage threshold key (strict).

    Raises:
        TypeError: When ``config[key]`` is present but not a
            non-bool int / float.
        ValueError: When the numeric value is non-finite or outside
            ``[_MIN_THRESHOLD_PCT, _MAX_THRESHOLD_PCT]``.
    """
    value = config.get(key)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"'{key}' must be numeric, got {type(value).__name__}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="throughput_adaptive",
            key=key,
            value=value,
        )
        raise TypeError(msg)
    if not math.isfinite(value) or not (
        _MIN_THRESHOLD_PCT <= value <= _MAX_THRESHOLD_PCT
    ):
        msg = (
            f"'{key}' must be between "
            f"{_MIN_THRESHOLD_PCT} and {_MAX_THRESHOLD_PCT}, "
            f"got {value}"
        )
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="throughput_adaptive",
            key=key,
            value=value,
        )
        raise ValueError(msg)


def validate_window_key(config: Mapping[str, Any]) -> None:
    """Validate measurement_window_tasks key (strict).

    Raises:
        TypeError: When the key is present but not a non-bool int.
        ValueError: When the integer is outside the allowed window
            range.
    """
    value = config.get(_KEY_MEASUREMENT_WINDOW_TASKS)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        msg = (
            f"'{_KEY_MEASUREMENT_WINDOW_TASKS}' must be an integer, "
            f"got {type(value).__name__}"
        )
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="throughput_adaptive",
            key=_KEY_MEASUREMENT_WINDOW_TASKS,
            value=value,
        )
        raise TypeError(msg)
    if not (_MIN_WINDOW_SIZE <= value <= _MAX_WINDOW_SIZE):
        msg = (
            f"'{_KEY_MEASUREMENT_WINDOW_TASKS}' must be between "
            f"{_MIN_WINDOW_SIZE} and {_MAX_WINDOW_SIZE}, got {value}"
        )
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="throughput_adaptive",
            key=_KEY_MEASUREMENT_WINDOW_TASKS,
            value=value,
        )
        raise ValueError(msg)
