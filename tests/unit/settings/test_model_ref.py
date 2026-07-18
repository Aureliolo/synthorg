"""Unit tests for the shared model-reference value + its shape validator."""

import pytest

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.errors import SettingValidationError
from synthorg.settings.model_ref import (
    ModelRef,
    parse_model_ref,
    serialize_model_ref,
)
from synthorg.settings.models import SettingDefinition
from synthorg.settings.type_validators import validate_by_type

pytestmark = pytest.mark.unit


def _defn() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_model",
        type=SettingType.MODEL_REF,
        default="",
        description="Decomposition model reference.",
        group="General",
        level=SettingLevel.ADVANCED,
    )


class TestParseModelRef:
    def test_empty_is_unset(self) -> None:
        ref = parse_model_ref("")
        assert ref == ModelRef()
        assert ref.is_bound is False

    def test_canonical_json_round_trips(self) -> None:
        ref = ModelRef(provider="example-provider", model_id="example-medium-001")
        assert parse_model_ref(serialize_model_ref(ref)) == ref
        assert ref.is_bound is True

    def test_bare_string_is_model_only(self) -> None:
        ref = parse_model_ref("example-medium-001")
        assert ref.provider == ""
        assert ref.model_id == "example-medium-001"
        assert ref.is_bound is False

    def test_unparseable_json_falls_back_to_model_only(self) -> None:
        ref = parse_model_ref("{not json")
        assert ref.model_id == "{not json"
        assert ref.provider == ""

    def test_missing_fields_default_to_empty(self) -> None:
        assert parse_model_ref('{"provider": "p"}') == ModelRef(provider="p")
        assert parse_model_ref('{"model_id": "m"}') == ModelRef(model_id="m")


class TestModelRefTypeValidation:
    def test_empty_accepted(self) -> None:
        # An empty value is "unset": the feature stays unwired, no dispatch.
        validate_by_type(_defn(), "")

    def test_bare_string_rejected(self) -> None:
        # A bare model id names no provider; a model assignment must bind
        # both so no dispatch can auto-select a provider for the id.
        with pytest.raises(SettingValidationError, match="provider is required"):
            validate_by_type(_defn(), "example-medium-001")

    def test_canonical_json_accepted(self) -> None:
        validate_by_type(
            _defn(),
            '{"provider": "example-provider", "model_id": "example-medium-001"}',
        )

    def test_blank_field_rejected(self) -> None:
        # A structured ref with either field blank is unbound and rejected.
        for value in (
            '{"provider": "", "model_id": "example-medium-001"}',
            '{"provider": "example-provider", "model_id": ""}',
            '{"provider": "  ", "model_id": "  "}',
        ):
            with pytest.raises(SettingValidationError, match="bind both"):
                validate_by_type(_defn(), value)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="model reference"):
            validate_by_type(_defn(), '{"provider": "p", "model_id": "m", "extra": 1}')

    def test_non_string_field_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="model reference"):
            validate_by_type(_defn(), '{"provider": "p", "model_id": 5}')

    def test_missing_required_field_rejected(self) -> None:
        # A structured value must carry BOTH keys; a partial dict is not a
        # valid model reference (write-path strictness).
        for value in ("{}", '{"provider": "p"}', '{"model_id": "m"}'):
            with pytest.raises(SettingValidationError, match="model reference"):
                validate_by_type(_defn(), value)

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="Invalid model reference"):
            validate_by_type(_defn(), '{"provider": ')
