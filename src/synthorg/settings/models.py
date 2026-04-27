"""Domain models for the settings persistence layer."""

import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
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
    """Validate a numeric constraint field (min_value/max_value)."""
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
            from env / YAML at process startup and rejects mutation via
            ``SettingsService.set()`` and friends.  The registry entry
            exists for discoverability so operators can introspect the
            value through the standard /settings API; mutation through
            that surface raises ``SettingReadOnlyError``.  Always implies
            ``restart_required=True``; the cross-field validator
            enforces the implication.
        enum_values: Allowed values when ``type`` is ``ENUM``.
        validator_pattern: Regex pattern for string validation.
        min_value: Minimum for numeric types (inclusive).
        max_value: Maximum for numeric types (inclusive).
        yaml_path: Dotted path into ``RootConfig`` for YAML resolution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
            "Sourced from env / YAML at startup; mutation via"
            " SettingsService is rejected. Implies restart_required=True."
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
    yaml_path: NotBlankStr | None = Field(
        default=None,
        description="Dotted path into RootConfig for YAML resolution",
    )

    @model_validator(mode="after")
    def _check_cross_field_constraints(self) -> SettingDefinition:
        """Validate cross-field invariants at construction time."""
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
                msg = f"Invalid validator_pattern: {exc}"
                raise ValueError(msg) from exc
        if self.default is not None:
            self._validate_default()
        return self

    def _validate_default(self) -> None:
        """Validate that the default value is consistent with the type."""
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
    """Check that *default* is parseable as *setting_type*."""
    validators = {
        SettingType.INTEGER: _check_default_int,
        SettingType.FLOAT: _check_default_float,
        SettingType.BOOLEAN: _check_default_bool,
        SettingType.JSON: _check_default_json,
    }
    validator = validators.get(setting_type)
    if validator is not None:
        validator(default)
    elif setting_type == SettingType.ENUM and default not in defn.enum_values:
        msg = f"default {default!r} not in enum_values"
        raise ValueError(msg)


def _check_default_int(default: str) -> None:
    try:
        int(default)
    except ValueError:
        msg = f"default {default!r} is not a valid integer"
        raise ValueError(msg) from None


def _check_default_float(default: str) -> None:
    try:
        val = float(default)
    except ValueError:
        msg = f"default {default!r} is not a valid float"
        raise ValueError(msg) from None
    if not math.isfinite(val):
        msg = f"default must be finite, got {val}"
        raise ValueError(msg)


def _check_default_bool(default: str) -> None:
    if default.lower() not in ("true", "false", "1", "0"):
        msg = f"default {default!r} is not a valid boolean"
        raise ValueError(msg)


def _check_default_json(default: str) -> None:
    try:
        json.loads(default)
    except json.JSONDecodeError:
        msg = "default is not valid JSON"
        raise ValueError(msg) from None


def _validate_default_range(
    default: str,
    defn: SettingDefinition,
) -> None:
    """Check numeric range constraints on a default value."""
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    definition: SettingDefinition = Field(description="Setting metadata")
    value: str = Field(description="Resolved value as string")
    source: SettingSource = Field(description="Value origin")
    updated_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp (DB values only)",
    )
