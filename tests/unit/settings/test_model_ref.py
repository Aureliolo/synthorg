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
        ref = ModelRef(provider="ollama-cloud", model_id="glm-5.2")
        assert parse_model_ref(serialize_model_ref(ref)) == ref
        assert ref.is_bound is True

    def test_bare_string_is_model_only(self) -> None:
        ref = parse_model_ref("glm-5.2")
        assert ref.provider == ""
        assert ref.model_id == "glm-5.2"
        assert ref.is_bound is False

    def test_unparseable_json_falls_back_to_model_only(self) -> None:
        ref = parse_model_ref("{not json")
        assert ref.model_id == "{not json"
        assert ref.provider == ""

    def test_missing_fields_default_to_empty(self) -> None:
        assert parse_model_ref('{"provider": "p"}') == ModelRef(provider="p")
        assert parse_model_ref('{"model_id": "m"}') == ModelRef(model_id="m")


class TestModelRefTypeValidation:
    def test_empty_and_bare_string_accepted(self) -> None:
        validate_by_type(_defn(), "")
        validate_by_type(_defn(), "glm-5.2")

    def test_canonical_json_accepted(self) -> None:
        validate_by_type(_defn(), '{"provider": "ollama-cloud", "model_id": "glm-5.2"}')

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="model reference"):
            validate_by_type(_defn(), '{"provider": "p", "model_id": "m", "extra": 1}')

    def test_non_string_field_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="model reference"):
            validate_by_type(_defn(), '{"provider": "p", "model_id": 5}')

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(SettingValidationError, match="Invalid model reference"):
            validate_by_type(_defn(), '{"provider": ')
