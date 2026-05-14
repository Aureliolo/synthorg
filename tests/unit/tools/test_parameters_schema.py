"""Tests for the ``ToolParametersSchema`` typed boundary."""

import pytest
from pydantic import ValidationError

from synthorg.tools.parameters_schema import ToolParametersSchema

pytestmark = pytest.mark.unit


class TestToolParametersSchemaValidate:
    """``ToolParametersSchema.model_validate`` boundary checks."""

    def test_accepts_empty_dict(self) -> None:
        schema = ToolParametersSchema.model_validate({})
        assert schema.root == {}

    def test_accepts_json_valued_dict(self) -> None:
        payload: dict[str, object] = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        schema = ToolParametersSchema.model_validate(payload)
        assert schema.root == payload

    def test_accepts_nested_arrays_and_nulls(self) -> None:
        payload: dict[str, object] = {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
            "examples": [1, 2.5, True, "x", None],
        }
        schema = ToolParametersSchema.model_validate(payload)
        assert schema.root == payload

    def test_rejects_non_dict_root(self) -> None:
        with pytest.raises(ValidationError):
            ToolParametersSchema.model_validate(["not", "a", "dict"])

    def test_rejects_non_json_value_in_dict(self) -> None:
        with pytest.raises(ValidationError):
            ToolParametersSchema.model_validate({"bad": {1, 2, 3}})

    def test_rejects_class_instance_value(self) -> None:
        class _Sentinel:
            pass

        with pytest.raises(ValidationError):
            ToolParametersSchema.model_validate({"bad": _Sentinel()})


class TestToolParametersSchemaAsDict:
    """``ToolParametersSchema.as_dict`` returns defensive deep copies."""

    def test_returns_equal_dict(self) -> None:
        payload: dict[str, object] = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        schema = ToolParametersSchema.model_validate(payload)
        assert schema.as_dict() == payload

    def test_returns_independent_copy(self) -> None:
        payload: dict[str, object] = {"properties": {"name": {"type": "string"}}}
        schema = ToolParametersSchema.model_validate(payload)
        copy1 = schema.as_dict()
        copy2 = schema.as_dict()
        assert copy1 is not copy2
        assert copy1["properties"] is not copy2["properties"]

    def test_caller_mutation_does_not_affect_root(self) -> None:
        payload: dict[str, object] = {"properties": {"name": {"type": "string"}}}
        schema = ToolParametersSchema.model_validate(payload)
        out = schema.as_dict()
        # Mutate both top-level and nested.
        out["new"] = "x"
        nested = out["properties"]
        assert isinstance(nested, dict)
        nested["injected"] = True
        # Original validated dict is untouched.
        assert "new" not in schema.root
        original_properties = schema.root["properties"]
        assert isinstance(original_properties, dict)
        assert "injected" not in original_properties
