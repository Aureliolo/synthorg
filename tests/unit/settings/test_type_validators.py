"""Unit tests for the SettingType validator registry."""

from typing import Any

import pytest

from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.errors import SettingValidationError
from synthorg.settings.models import SettingDefinition
from synthorg.settings.type_validators import (
    TYPE_VALIDATORS,
    validate_by_type,
)

_UNSET = "__UNSET__"


def _make_definition(  # noqa: PLR0913
    *,
    setting_type: SettingType = SettingType.STRING,
    default: str | None = _UNSET,
    sensitive: bool = False,
    enum_values: tuple[str, ...] = (),
    min_value: float | None = None,
    max_value: float | None = None,
    namespace: SettingNamespace = SettingNamespace.BUDGET,
    key: str = "test_key",
) -> SettingDefinition:
    """Build a SettingDefinition with sensible defaults per type."""
    if default == _UNSET:
        # Suppress the auto-default when range constraints would reject it;
        # the validator-under-test cares about the runtime value, not the
        # registered default.
        if min_value is not None or max_value is not None:
            resolved_default = None
        else:
            resolved_default = _default_for(setting_type, enum_values)
    else:
        resolved_default = default
    return SettingDefinition(
        namespace=namespace,
        key=key,
        type=setting_type,
        default=resolved_default,
        description="test",
        group="test",
        sensitive=sensitive,
        enum_values=enum_values,
        min_value=min_value,
        max_value=max_value,
    )


def _default_for(
    setting_type: SettingType,
    enum_values: tuple[str, ...],
) -> str | None:
    """Pick a default value the SettingDefinition validator will accept."""
    if setting_type == SettingType.INTEGER:
        return "0"
    if setting_type == SettingType.FLOAT:
        return "0.0"
    if setting_type == SettingType.BOOLEAN:
        return "false"
    if setting_type == SettingType.JSON:
        return "{}"
    if setting_type == SettingType.ENUM:
        return enum_values[0] if enum_values else None
    return None


# ── Registry exhaustiveness ──────────────────────────────────────


@pytest.mark.unit
class TestRegistryShape:
    """The registry must be total over SettingType."""

    def test_registry_covers_every_setting_type(self) -> None:
        assert set(TYPE_VALIDATORS.keys()) == set(SettingType)

    def test_registry_raises_keyerror_for_unknown_type(self) -> None:
        """Defensive: registry-lookup, not silent fall-through, governs dispatch."""
        with pytest.raises(KeyError):
            _ = TYPE_VALIDATORS[object()]  # type: ignore[index]


# ── STRING (no-op) ───────────────────────────────────────────────


@pytest.mark.unit
class TestValidateString:
    @pytest.mark.parametrize("value", ["anything", "", "with spaces", "0", "false"])
    def test_string_is_no_op(self, value: str) -> None:
        # No assertion needed: validate_by_type returns None and STRING raises
        # nothing for any input. The test passes iff the call doesn't raise.
        validate_by_type(_make_definition(setting_type=SettingType.STRING), value)


# ── INTEGER ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateInteger:
    @pytest.mark.parametrize("value", ["42", "-7", "0", "1000000"])
    def test_accepts_integer_strings(self, value: str) -> None:
        validate_by_type(_make_definition(setting_type=SettingType.INTEGER), value)

    @pytest.mark.parametrize("value", ["abc", "3.14", "", "1e10", "0x10"])
    def test_rejects_non_integer(self, value: str) -> None:
        with pytest.raises(SettingValidationError, match="Expected integer"):
            validate_by_type(
                _make_definition(setting_type=SettingType.INTEGER),
                value,
            )

    def test_sensitive_value_masked_in_error(self) -> None:
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(
                _make_definition(setting_type=SettingType.INTEGER, sensitive=True),
                "secret-value",
            )
        assert "secret-value" not in str(exc_info.value)
        assert "********" in str(exc_info.value)

    def test_below_min_rejected(self) -> None:
        definition = _make_definition(setting_type=SettingType.INTEGER, min_value=10.0)
        with pytest.raises(SettingValidationError, match="below minimum"):
            validate_by_type(definition, "5")

    def test_above_max_rejected(self) -> None:
        definition = _make_definition(setting_type=SettingType.INTEGER, max_value=10.0)
        with pytest.raises(SettingValidationError, match="above maximum"):
            validate_by_type(definition, "15")

    def test_inside_range_accepted(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.INTEGER,
            min_value=0.0,
            max_value=10.0,
        )
        validate_by_type(definition, "5")

    def test_at_min_inclusive(self) -> None:
        definition = _make_definition(setting_type=SettingType.INTEGER, min_value=5.0)
        validate_by_type(definition, "5")

    def test_at_max_inclusive(self) -> None:
        definition = _make_definition(setting_type=SettingType.INTEGER, max_value=5.0)
        validate_by_type(definition, "5")

    def test_huge_integer_does_not_overflow(self) -> None:
        """``float(int("9"*4000))`` raises OverflowError; range check must not."""
        definition = _make_definition(
            setting_type=SettingType.INTEGER,
            max_value=10.0,
        )
        with pytest.raises(SettingValidationError, match="above maximum"):
            validate_by_type(definition, "9" * 4000)

    def test_huge_integer_passes_when_unbounded(self) -> None:
        validate_by_type(
            _make_definition(setting_type=SettingType.INTEGER),
            "9" * 4000,
        )


# ── FLOAT ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateFloat:
    @pytest.mark.parametrize("value", ["3.14", "5", "-2.5", "0", "0.0", "1e10"])
    def test_accepts_float_strings(self, value: str) -> None:
        validate_by_type(_make_definition(setting_type=SettingType.FLOAT), value)

    @pytest.mark.parametrize("value", ["abc", "", "3.14.15", "0x10"])
    def test_rejects_non_numeric(self, value: str) -> None:
        with pytest.raises(SettingValidationError, match="Expected float"):
            validate_by_type(
                _make_definition(setting_type=SettingType.FLOAT),
                value,
            )

    @pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity"])
    def test_rejects_non_finite(self, value: str) -> None:
        with pytest.raises(SettingValidationError, match="Expected finite float"):
            validate_by_type(
                _make_definition(setting_type=SettingType.FLOAT),
                value,
            )

    def test_non_finite_rejected_before_range_check(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.FLOAT,
            min_value=0.0,
            max_value=10.0,
        )
        with pytest.raises(SettingValidationError, match="Expected finite float"):
            validate_by_type(definition, "nan")

    def test_non_finite_sensitive_value_masked(self) -> None:
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(
                _make_definition(setting_type=SettingType.FLOAT, sensitive=True),
                "inf",
            )
        # The displayed payload must be the mask, not the literal "inf".
        # The error message text itself contains "finite" but does not
        # embed an "inf" substring on its own.
        message = str(exc_info.value)
        assert "********" in message
        assert "'inf'" not in message

    def test_below_min_rejected(self) -> None:
        definition = _make_definition(setting_type=SettingType.FLOAT, min_value=1.0)
        with pytest.raises(SettingValidationError, match="below minimum"):
            validate_by_type(definition, "0.5")

    def test_sensitive_value_masked(self) -> None:
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(
                _make_definition(setting_type=SettingType.FLOAT, sensitive=True),
                "leak-this",
            )
        assert "leak-this" not in str(exc_info.value)


# ── BOOLEAN ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateBoolean:
    @pytest.mark.parametrize("value", ["true", "false", "1", "0", "TRUE", "False"])
    def test_accepts_canonical_values(self, value: str) -> None:
        validate_by_type(_make_definition(setting_type=SettingType.BOOLEAN), value)

    @pytest.mark.parametrize("value", ["yes", "no", "on", "off", "", "2"])
    def test_rejects_non_canonical(self, value: str) -> None:
        with pytest.raises(SettingValidationError, match="Expected boolean"):
            validate_by_type(
                _make_definition(setting_type=SettingType.BOOLEAN),
                value,
            )

    def test_sensitive_value_masked(self) -> None:
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(
                _make_definition(setting_type=SettingType.BOOLEAN, sensitive=True),
                "secret",
            )
        assert "secret" not in str(exc_info.value)


# ── ENUM ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateEnum:
    def test_accepts_member(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.ENUM,
            enum_values=("alpha", "beta", "gamma"),
        )
        validate_by_type(definition, "alpha")

    def test_rejects_non_member(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.ENUM,
            enum_values=("alpha", "beta"),
        )
        with pytest.raises(SettingValidationError, match="Invalid enum value"):
            validate_by_type(definition, "delta")

    def test_sensitive_value_masked(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.ENUM,
            enum_values=("alpha", "beta"),
            sensitive=True,
        )
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(definition, "secret-leak")
        assert "secret-leak" not in str(exc_info.value)


# ── JSON ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateJson:
    def test_accepts_valid_json(self) -> None:
        validate_by_type(_make_definition(setting_type=SettingType.JSON), '{"a":1}')

    def test_accepts_array(self) -> None:
        validate_by_type(_make_definition(setting_type=SettingType.JSON), "[1,2,3]")

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(SettingValidationError, match="Invalid JSON"):
            validate_by_type(
                _make_definition(setting_type=SettingType.JSON),
                "not json",
            )

    def test_no_shape_validator_passes_when_parseable(self) -> None:
        """With no per-setting shape validator registered, parseable JSON passes."""
        definition = _make_definition(
            setting_type=SettingType.JSON,
            namespace=SettingNamespace.BUDGET,
            key="unregistered_for_shape",
        )
        validate_by_type(definition, '{"x": 1}')

    def test_shape_validator_invoked_after_parse(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-setting shape validator runs after json.loads succeeds."""

        def _shape(value: Any) -> None:
            if not isinstance(value, dict):
                msg = "expected object"
                raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError

        from synthorg.settings import json_validators as jv

        monkeypatch.setitem(
            jv._JSON_VALIDATORS,
            (SettingNamespace.BUDGET.value, "budget_shape_test"),
            _shape,
        )
        definition = _make_definition(
            setting_type=SettingType.JSON,
            key="budget_shape_test",
        )
        validate_by_type(definition, '{"ok": true}')
        with pytest.raises(SettingValidationError, match="Invalid JSON shape"):
            validate_by_type(definition, "[1,2,3]")

    def test_shape_validator_masks_sensitive_in_parse_error(self) -> None:
        definition = _make_definition(
            setting_type=SettingType.JSON,
            sensitive=True,
        )
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(definition, "not json")
        assert "Invalid JSON for sensitive setting" in str(exc_info.value)

    def test_shape_validator_masks_sensitive_in_shape_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _always_fails(_value: Any) -> None:
            msg = "shape mismatch"
            raise ValueError(msg)

        from synthorg.settings import json_validators as jv

        monkeypatch.setitem(
            jv._JSON_VALIDATORS,
            (SettingNamespace.BUDGET.value, "sensitive_shape_test"),
            _always_fails,
        )
        definition = _make_definition(
            setting_type=SettingType.JSON,
            sensitive=True,
            key="sensitive_shape_test",
        )
        with pytest.raises(SettingValidationError) as exc_info:
            validate_by_type(definition, '{"x":1}')
        assert "Invalid JSON shape for sensitive setting" in str(exc_info.value)
