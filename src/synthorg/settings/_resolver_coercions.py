# module-kind: code
"""Value-coercion and small construction helpers for ``ConfigResolver``.

Holds the boundary coercions that reject malformed stored settings
deterministically (numeric VRAM / batch-size shapes, boolean strings)
plus the budget-alert construction helper that turns three resolved
threshold integers into a validated ``BudgetAlertConfig``. These live
beside ``ConfigResolver`` rather than inside it so the resolver spine
stays focused on the per-kind accessors and the composed getters.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_VALIDATION_FAILED

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetAlertConfig

logger = get_logger(__name__)

_BOOL_TRUE = frozenset({"true", "1"})
_BOOL_FALSE = frozenset({"false", "0"})


def _coerce_vram_gb(value: object) -> float:
    """Coerce a parsed JSON value to a numeric VRAM threshold.

    Plain ``float(value)`` would accept ``True`` / ``False`` (because
    ``bool`` is an ``int`` subclass and ``int`` is float-coercible), so
    a payload like ``[true, 64]`` would silently become ``(1.0, 64)``
    and pass the remaining shape checks. Reject booleans and
    non-numeric types at the boundary so invalid stored settings fail
    deterministically.

    Returns:
        The value coerced to ``float``, guaranteed to be a non-boolean
        numeric type.

    Raises:
        TypeError: If *value* is a ``bool`` or any non-numeric type.
    """
    if isinstance(value, bool):
        msg = f"vram_gb must be numeric, got bool {value!r}"
        raise TypeError(msg)
    if not isinstance(value, int | float):
        msg = f"vram_gb must be numeric, got {type(value).__name__} {value!r}"
        raise TypeError(msg)
    return float(value)


def _coerce_batch_size(value: object) -> int:
    """Coerce a parsed JSON value to an ``int`` batch size, rejecting bad shapes.

    Plain ``int(value)`` would silently truncate ``64.9`` to ``64`` and
    accept ``True`` / ``False`` (which are ``int`` subclasses), so a
    typo in ``memory.fine_tune_vram_batch_table`` would apply with a
    different value than the operator configured. Reject those at the
    boundary so invalid stored settings fail deterministically.

    Returns:
        The value coerced to ``int``, guaranteed to be a whole-number
        non-boolean numeric type.

    Raises:
        TypeError: If *value* is a ``bool`` or any non-numeric type.
        ValueError: If *value* is a fractional (non-integer) float.
    """
    if isinstance(value, bool):
        msg = f"batch_size must be an integer, got bool {value!r}"
        raise TypeError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            msg = f"batch_size must be an integer, got fractional float {value!r}"
            raise ValueError(msg)
        return int(value)
    msg = f"batch_size must be an integer, got {type(value).__name__} {value!r}"
    raise TypeError(msg)


def _build_budget_alerts(warn: int, crit: int, stop: int) -> BudgetAlertConfig:
    """Construct ``BudgetAlertConfig`` with ordering validation.

    Args:
        warn: Warning threshold percent.
        crit: Critical threshold percent.
        stop: Hard-stop threshold percent.

    Returns:
        A validated ``BudgetAlertConfig``.

    Raises:
        ValueError: If the thresholds violate the ordering constraint
            (``warn < crit < stop``).
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from synthorg.budget.config import BudgetAlertConfig  # noqa: PLC0415

    try:
        return BudgetAlertConfig(warn_at=warn, critical_at=crit, hard_stop_at=stop)
    except ValidationError as exc:
        logger.warning(
            SETTINGS_VALIDATION_FAILED,
            namespace="budget",
            key="_alerts",
            reason="threshold_ordering",
        )
        msg = "Budget alert thresholds must satisfy warn < critical < hard_stop"
        raise ValueError(msg) from exc


def _parse_bool(value: str) -> bool:
    """Parse a string into a boolean.

    Accepts ``"true"``/``"false"``/``"1"``/``"0"``
    (case-insensitive).

    Args:
        value: String to parse.

    Returns:
        The parsed boolean.

    Raises:
        ValueError: If the string is not a recognised boolean.
    """
    lower = value.lower()
    if lower in _BOOL_TRUE:
        return True
    if lower in _BOOL_FALSE:
        return False
    msg = "Value is not a recognized boolean string"
    raise ValueError(msg)
