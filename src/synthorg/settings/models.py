"""Domain models for the settings persistence layer."""

import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.registry import StrategyRegistry
from synthorg.core.types import NotBlankStr
from synthorg.observability import safe_error_description
from synthorg.settings.enums import (
    SettingLevel,
    SettingNamespace,
    SettingSource,
    SettingType,
)


def _check_numeric_field(
    value: float | None,
    name: str,
    setting_type: SettingType,
) -> None:
    """Validate a numeric constraint field (min_value/max_value).

    Raises:
        ValueError: If a bound is supplied for a non-numeric
            ``setting_type``, or the bound is non-finite (NaN/inf).
    """
    if value is None:
        return
    if setting_type not in (SettingType.INTEGER, SettingType.FLOAT):
        msg = f"{name} is only valid for INTEGER/FLOAT, not {setting_type}"
        raise ValueError(msg)
    if not math.isfinite(value):
        msg = f"{name} must be finite, got {value}"
        raise ValueError(msg)


class SettingDefinition(BaseModel):
    """Metadata for a single registered setting.

    Drives validation, UI generation, and schema introspection.
    All values are stored as strings; ``type`` controls coercion.

    Attributes:
        namespace: Setting namespace (subsystem grouping).
        key: Setting key within the namespace.
        type: Data type for validation and coercion.
        default: Default value serialised as a string, or ``None``.
        description: Human-readable description.
        group: UI grouping label (e.g. ``"Limits"``).
        level: Visibility level for progressive disclosure.
        sensitive: Whether the value should be encrypted at rest.
        restart_required: Whether changes require a restart.
        read_only_post_init: Whether the setting is sourced exclusively
            from the environment at process startup and rejects mutation
            via ``SettingsService.set()`` and friends.  The registry
            entry exists for discoverability so operators can introspect
            the value through the standard /settings API; mutation
            through that surface raises ``SettingReadOnlyError``.
            Always implies ``restart_required=True``; the cross-field
            validator enforces the implication.
        enum_values: Allowed values when ``type`` is ``ENUM``.
        validator_pattern: Regex pattern for string validation.
        min_value: Minimum for numeric types (inclusive).
        max_value: Maximum for numeric types (inclusive).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    namespace: SettingNamespace = Field(description="Setting namespace")
    key: NotBlankStr = Field(description="Setting key within namespace")
    type: SettingType = Field(description="Value data type")
    default: str | None = Field(
        default=None,
        description="Default value as string",
    )
    description: NotBlankStr = Field(description="Human-readable description")
    group: NotBlankStr = Field(description="UI grouping label")
    level: SettingLevel = Field(
        default=SettingLevel.BASIC,
        description="Visibility level",
    )
    sensitive: bool = Field(
        default=False,
        description="Encrypt at rest and mask in UI",
    )
    restart_required: bool = Field(
        default=False,
        description="Change takes effect after restart",
    )
    read_only_post_init: bool = Field(
        default=False,
        description=(
            "Sourced from environment at startup; mutation via"
            " SettingsService is rejected. Implies restart_required=True."
        ),
    )
    env_var_override: NotBlankStr | None = Field(
        default=None,
        description=(
            "Override the auto-derived ``SYNTHORG_{NAMESPACE}_{KEY}``"
            " env var name with a custom one (e.g. ``SYNTHORG_LOG_DIR``"
            " for ``observability.log_directory``).  Used when an"
            " established operator-facing env var name predates the"
            " auto-derivation rule.  When set, the resolver looks up"
            " *only* this name; the auto-derived name is not"
            " consulted."
        ),
    )
    enum_values: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Allowed values for ENUM type",
    )
    validator_pattern: NotBlankStr | None = Field(
        default=None,
        max_length=256,
        description="Regex pattern for string validation",
    )
    min_value: float | None = Field(
        default=None,
        description="Minimum value for numeric types",
    )
    max_value: float | None = Field(
        default=None,
        description="Maximum value for numeric types",
    )

    @model_validator(mode="after")
    def _check_cross_field_constraints(self) -> SettingDefinition:
        """Validate cross-field invariants at construction time.

        Returns:
            The validated ``SettingDefinition`` (Pydantic
            ``model_validator(mode="after")`` contract).

        Raises:
            ValueError: If an ENUM setting has empty ``enum_values``;
                ``read_only_post_init`` is set without
                ``restart_required``; a numeric bound is invalid or
                non-finite; ``min_value`` exceeds ``max_value``;
                ``validator_pattern`` is not a valid regex; or the
                default value fails any of these constraints.
        """
        if self.type == SettingType.ENUM and not self.enum_values:
            msg = (
                f"ENUM setting {self.namespace}/{self.key}"
                f" requires non-empty enum_values"
            )
            raise ValueError(msg)
        if self.read_only_post_init and not self.restart_required:
            # read_only_post_init implies the value is baked in at boot;
            # callers will hit confusing UX if they see it succeed at
            # registration but reject mutation later. Force the implied
            # invariant so misconfiguration fails at definition time.
            msg = (
                f"Setting {self.namespace}/{self.key} marked"
                f" read_only_post_init must also set restart_required=True"
            )
            raise ValueError(msg)
        _check_numeric_field(self.min_value, "min_value", self.type)
        _check_numeric_field(self.max_value, "max_value", self.type)
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            msg = f"min_value ({self.min_value}) exceeds max_value ({self.max_value})"
            raise ValueError(msg)
        if self.validator_pattern is not None:
            try:
                re.compile(self.validator_pattern)
            except re.error as exc:
                msg = f"Invalid validator_pattern: {safe_error_description(exc)}"
                raise ValueError(msg) from exc
        if self.default is not None:
            self._validate_default()
        return self

    def _validate_default(self) -> None:
        """Validate that the default value is consistent with the type.

        Raises:
            ValueError: If the default is not parseable as the declared
                type, falls outside the declared numeric range, or does
                not match the ``validator_pattern`` regex.
        """
        default = self.default
        if default is None:
            return
        _validate_default_type(self.type, default, self)
        _validate_default_range(default, self)
        if self.validator_pattern is not None and not re.fullmatch(
            self.validator_pattern, default
        ):
            msg = f"default {default!r} does not match validator_pattern"
            raise ValueError(msg)


def _validate_default_type(
    setting_type: SettingType,
    default: str,
    defn: SettingDefinition,
) -> None:
    """Check that *default* is parseable as *setting_type*.

    STRING needs no parse check; ENUM is a membership check against
    ``defn.enum_values``. Both are resolved inline so they never reach
    ``_DEFAULT_TYPE_CHECK_REGISTRY.get`` (a registry miss logs
    ``REGISTRY_FACTORY_NOT_FOUND`` at ERROR before raising, which at
    boot fired once per STRING/ENUM-with-default definition). Scalar
    parse checks for INTEGER/FLOAT/BOOLEAN/JSON dispatch through the
    registry, which always hits.

    Raises:
        ValueError: If ``default`` is not a member of ``enum_values``
            for an ENUM type, or is not parseable as the declared
            scalar type.
    """
    if setting_type == SettingType.STRING:
        return
    if setting_type == SettingType.ENUM:
        if default not in defn.enum_values:
            msg = f"default {default!r} not in enum_values"
            raise ValueError(msg)
        return
    check = _DEFAULT_TYPE_CHECK_REGISTRY.get(setting_type)
    check(default)


def _check_default_int(default: str) -> None:
    """Check that *default* parses as an integer.

    Raises:
        ValueError: If *default* is not a valid integer string.
    """
    try:
        int(default)
    except ValueError:
        msg = f"default {default!r} is not a valid integer"
        raise ValueError(msg) from None


def _check_default_float(default: str) -> None:
    """Check that *default* parses as a finite float.

    Raises:
        ValueError: If *default* is not a valid float string, or parses
            to a non-finite value (NaN/inf).
    """
    try:
        val = float(default)
    except ValueError:
        msg = f"default {default!r} is not a valid float"
        raise ValueError(msg) from None
    if not math.isfinite(val):
        msg = f"default must be finite, got {val}"
        raise ValueError(msg)


def _check_default_bool(default: str) -> None:
    """Check that *default* is a recognised boolean spelling.

    Raises:
        ValueError: If *default* is not ``true``/``false``/``1``/``0``
            (case-insensitive).
    """
    if default.lower() not in ("true", "false", "1", "0"):
        msg = f"default {default!r} is not a valid boolean"
        raise ValueError(msg)


def _check_default_json(default: str) -> None:
    """Check that *default* parses as JSON.

    Raises:
        ValueError: If *default* is not valid JSON.
    """
    try:
        json.loads(default)
    except json.JSONDecodeError:
        msg = "default is not valid JSON"
        raise ValueError(msg) from None


# Keyed by ``SettingType`` (a ``StrEnum``). ``.get`` (not ``.build``)
# is used at dispatch so a checker's expected ``ValueError`` rejection
# of a malformed registered default does not emit a registry
# ``factory.failed`` warning.
_DEFAULT_TYPE_CHECK_REGISTRY: StrategyRegistry[None] = StrategyRegistry(
    {
        SettingType.INTEGER: _check_default_int,
        SettingType.FLOAT: _check_default_float,
        SettingType.BOOLEAN: _check_default_bool,
        SettingType.JSON: _check_default_json,
    },
    kind="setting_default_type_check",
)


def _validate_default_range(
    default: str,
    defn: SettingDefinition,
) -> None:
    """Check numeric range constraints on a default value.

    Raises:
        ValueError: If the numeric default is below ``min_value`` or
            above ``max_value``.
    """
    if defn.type not in (SettingType.INTEGER, SettingType.FLOAT):
        return
    val = float(default)
    if defn.min_value is not None and val < defn.min_value:
        msg = f"default {val} below min_value {defn.min_value}"
        raise ValueError(msg)
    if defn.max_value is not None and val > defn.max_value:
        msg = f"default {val} above max_value {defn.max_value}"
        raise ValueError(msg)


class SettingValue(BaseModel):
    """A resolved setting value with its origin.

    Attributes:
        namespace: Setting namespace.
        key: Setting key.
        value: Resolved value as a string.
        source: Where the value came from.
        updated_at: ISO 8601 timestamp for DB-sourced values.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    namespace: SettingNamespace = Field(description="Setting namespace")
    key: NotBlankStr = Field(description="Setting key")
    value: str = Field(description="Resolved value as string")
    source: SettingSource = Field(description="Value origin")
    updated_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp (DB values only)",
    )


class SettingEntry(BaseModel):
    """Combined view of a setting definition and its resolved value.

    Used by the API to return full setting information in a
    single response object.

    Attributes:
        definition: Setting metadata.
        value: Resolved value as a string.
        source: Where the value came from.
        updated_at: ISO 8601 timestamp for DB-sourced values.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    definition: SettingDefinition = Field(description="Setting metadata")
    value: str = Field(description="Resolved value as string")
    source: SettingSource = Field(description="Value origin")
    updated_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp (DB values only)",
    )
