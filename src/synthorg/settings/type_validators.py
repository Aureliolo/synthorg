"""Per-`SettingType` value validators dispatched through a registry.

Each :class:`~synthorg.settings.enums.SettingType` member is mapped to a
callable in :data:`TYPE_VALIDATORS`; :func:`validate_by_type` is the
public entry point.  The registry is total over ``SettingType`` so a
new variant must register a handler (or an explicit no-op) before the
validation surface compiles cleanly.

Layered with :mod:`synthorg.settings.json_validators` for JSON shape
checks: this module covers parseability + range; the per-setting JSON
shape validators run after a successful parse for a tighter contract.
"""

import json
import math
from typing import TYPE_CHECKING, Final

from synthorg.core.registry import StrategyRegistry
from synthorg.observability import safe_error_description
from synthorg.settings.enums import SettingType
from synthorg.settings.errors import SettingValidationError
from synthorg.settings.json_validators import get_json_validator

if TYPE_CHECKING:
    from synthorg.settings.models import SettingDefinition

_SENSITIVE_MASK: Final[str] = "********"


def _validate_string(definition: SettingDefinition, value: str) -> None:
    """Accept any string; pattern + length checks happen in ``_validate_value``."""
    del definition, value


def _validate_integer(definition: SettingDefinition, value: str) -> None:
    """Validate that *value* parses as an integer within range.

    Raises:
        SettingValidationError: If *value* is not an integer, or it falls
            outside the definition's ``min_value`` / ``max_value`` bounds.
    """
    try:
        int_val = int(value)
    except ValueError as exc:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected integer, got {display}"
        raise SettingValidationError(msg) from exc
    # Pass the int directly: ``float(huge_int)`` raises OverflowError for
    # arbitrarily-large ints, and Python's int↔float comparisons work
    # natively against the ``min_value`` / ``max_value`` bounds.
    _check_range(definition, int_val)


def _validate_float(definition: SettingDefinition, value: str) -> None:
    """Validate that *value* parses as a finite float within range.

    Raises:
        SettingValidationError: If *value* is not a float, is non-finite
            (NaN/inf), or falls outside the definition's ``min_value`` /
            ``max_value`` bounds.
    """
    try:
        float_val = float(value)
    except ValueError as exc:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected float, got {display}"
        raise SettingValidationError(msg) from exc
    if not math.isfinite(float_val):
        # NaN comparisons silently return False, so _check_range can't reject
        # nan/inf -- guard explicitly to keep range constraints meaningful.
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected finite float, got {display}"
        raise SettingValidationError(msg)
    _check_range(definition, float_val)


def _validate_boolean(definition: SettingDefinition, value: str) -> None:
    """Validate that *value* is a recognised boolean spelling.

    Raises:
        SettingValidationError: If *value* is not one of ``true``,
            ``false``, ``1`` or ``0`` (case-insensitive).
    """
    if value.lower() not in ("true", "false", "1", "0"):
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected boolean, got {display}"
        raise SettingValidationError(msg)


def _validate_enum(definition: SettingDefinition, value: str) -> None:
    """Validate that *value* is one of the definition's allowed enum values.

    Raises:
        SettingValidationError: If *value* is not in
            ``definition.enum_values``.
    """
    if value not in definition.enum_values:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Invalid enum value {display}. Allowed: {definition.enum_values}"
        raise SettingValidationError(msg)


def _validate_json(definition: SettingDefinition, value: str) -> None:
    """Validate that *value* is parseable JSON of the registered shape.

    Parseability is checked first; a successful parse is then passed to
    any per-setting shape validator from :mod:`json_validators`. Both
    failure modes surface as ``SettingValidationError`` so the write-time
    error surface stays uniform, with sensitive payloads masked.

    Raises:
        SettingValidationError: If *value* is not valid JSON, or the
            per-setting shape validator rejects the parsed payload.
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        if definition.sensitive:
            msg = (
                f"Invalid JSON for sensitive setting"
                f" {definition.namespace}/{definition.key}"
            )
        else:
            msg = f"Invalid JSON: {safe_error_description(exc)}"
        raise SettingValidationError(msg) from exc
    # Dispatch to any per-setting shape validator so write-time and
    # runtime contracts stay aligned (e.g. canonical-origin checks for
    # ``api.csp_docs_external_origins``). Validators raise ``ValueError``
    # which we re-wrap as ``SettingValidationError`` to keep the error
    # surface uniform; sensitive payloads are masked the same way the
    # parse-error branch above masks them.
    validator = get_json_validator(str(definition.namespace), definition.key)
    if validator is None:
        return
    try:
        validator(parsed)
    except ValueError as exc:
        if definition.sensitive:
            msg = (
                f"Invalid JSON shape for sensitive setting"
                f" {definition.namespace}/{definition.key}"
            )
        else:
            msg = (
                f"Invalid JSON shape for {definition.namespace}/{definition.key}:"
                f" {safe_error_description(exc)}"
            )
        raise SettingValidationError(msg) from exc


def _check_range(definition: SettingDefinition, value: int | float) -> None:
    """Check numeric range constraints.

    Raises:
        SettingValidationError: If *value* is below ``min_value`` or above
            ``max_value`` when those bounds are set.
    """
    display = _SENSITIVE_MASK if definition.sensitive else str(value)
    if definition.min_value is not None and value < definition.min_value:
        msg = f"Value {display} below minimum {definition.min_value}"
        raise SettingValidationError(msg)
    if definition.max_value is not None and value > definition.max_value:
        msg = f"Value {display} above maximum {definition.max_value}"
        raise SettingValidationError(msg)


# Keyed by ``SettingType`` (a ``StrEnum``). ``.get`` (not ``.build``)
# is used at dispatch so a validator's expected ``SettingValidationError``
# rejection does not emit a registry ``factory.failed`` warning on every
# invalid operator value.
_TYPE_VALIDATOR_REGISTRY: Final[StrategyRegistry[None]] = StrategyRegistry(
    {
        SettingType.STRING: _validate_string,
        SettingType.INTEGER: _validate_integer,
        SettingType.FLOAT: _validate_float,
        SettingType.BOOLEAN: _validate_boolean,
        SettingType.ENUM: _validate_enum,
        SettingType.JSON: _validate_json,
    },
    kind="setting_type_validator",
)


def validate_by_type(definition: SettingDefinition, value: str) -> None:
    """Type-specific validation dispatch.

    Looks up the registered validator for ``definition.type`` and runs
    it. Raises :class:`SettingValidationError` on type mismatch, range
    violation, or JSON-shape failure.

    Raises :class:`~synthorg.core.registry.StrategyFactoryNotFoundError`
    for an unregistered ``SettingType`` -- a defensive contract so
    adding a new enum variant without wiring a handler fails loudly
    instead of silently passing validation.
    """
    _TYPE_VALIDATOR_REGISTRY.get(definition.type)(definition, value)
