"""Tests for nullable union normalization in OpenAPI schema post-processing.

Verifies that :func:`_normalize_nullable_unions` correctly flattens
``oneOf``/``anyOf`` nullable unions to JSON Schema 2020-12 ``type``
arrays, inlines enum ``$ref`` targets, and collapses redundant unions.
"""

from typing import cast

import pytest

from synthorg.api.openapi import inject_rfc9457_responses
from synthorg.api.openapi_normalize import _normalize_nullable_unions
from tests._shared import JsonDict


def _minimal_schema(
    *,
    extra_schemas: JsonDict | None = None,
) -> JsonDict:
    """Build a minimal OpenAPI schema dict for normalization tests."""
    schemas: JsonDict = {
        "ErrorCode": {"type": "integer", "enum": [1000, 3001]},
        "ErrorCategory": {"type": "string", "enum": ["auth", "not_found"]},
        "ErrorDetail": {"type": "object", "properties": {}},
        "ApiResponse_NoneType_": {"type": "object", "properties": {}},
    }
    if extra_schemas:
        schemas.update(extra_schemas)
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "0.1.0"},
        "paths": {},
        "components": {"schemas": schemas},
    }


@pytest.mark.unit
class TestNullableUnionNormalization:
    """Nullable oneOf/anyOf unions are flattened to type arrays."""

    def test_primitive_oneof_flattened(self) -> None:
        """oneOf with primitive + null becomes type array."""
        schema: JsonDict = {
            "oneOf": [{"type": "string"}, {"type": "null"}],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert result == {"type": ["string", "null"]}

    def test_primitive_anyof_flattened(self) -> None:
        """anyOf with primitive + null becomes type array."""
        schema: JsonDict = {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert result == {"type": ["integer", "null"]}

    def test_constraints_preserved(self) -> None:
        """Extra properties (minLength, format) are kept."""
        schema: JsonDict = {
            "oneOf": [
                {"type": "string", "format": "date-time"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert result == {"type": ["string", "null"], "format": "date-time"}

    def test_enum_ref_inlined(self) -> None:
        """$ref to enum + null inlines enum values and flattens."""
        all_schemas: JsonDict = {
            "Status": {
                "type": "string",
                "enum": ["active", "inactive"],
                "title": "Status",
            },
        }
        schema: JsonDict = {
            "description": "Current status",
            "oneOf": [
                {"$ref": "#/components/schemas/Status"},
                {"type": "null"},
            ],
        }
        result = cast(
            JsonDict, _normalize_nullable_unions(schema, all_schemas=all_schemas)
        )
        assert result["type"] == ["string", "null"]
        assert result["enum"] == ["active", "inactive", None]
        assert result["description"] == "Current status"

    def test_enum_ref_without_description(self) -> None:
        """$ref to enum + null without description omits description key."""
        all_schemas: JsonDict = {
            "Status": {
                "type": "string",
                "enum": ["on", "off"],
                "title": "Status",
            },
        }
        schema: JsonDict = {
            "oneOf": [
                {"$ref": "#/components/schemas/Status"},
                {"type": "null"},
            ],
        }
        result = cast(
            JsonDict, _normalize_nullable_unions(schema, all_schemas=all_schemas)
        )
        assert result["type"] == ["string", "null"]
        assert "description" not in result

    def test_object_ref_becomes_anyof(self) -> None:
        """$ref to object + null uses anyOf (known renderer limitation)."""
        schema: JsonDict = {
            "oneOf": [
                {"$ref": "#/components/schemas/Minutes"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert "anyOf" in result
        assert "oneOf" not in result

    def test_object_ref_anyof_with_registry_stays_anyof(self) -> None:
        """anyOf with non-enum $ref + null stays anyOf when registry provided."""
        all_schemas: JsonDict = {
            "Minutes": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        }
        schema: JsonDict = {
            "anyOf": [
                {"$ref": "#/components/schemas/Minutes"},
                {"type": "null"},
            ],
        }
        result = cast(
            JsonDict, _normalize_nullable_unions(schema, all_schemas=all_schemas)
        )
        assert "anyOf" in result
        assert len(result["anyOf"]) == 2

    def test_ref_with_non_component_prefix_not_inlined(self) -> None:
        """$ref with non-#/components/schemas/ prefix falls through."""
        all_schemas: JsonDict = {
            "Foo": {"type": "string", "enum": ["a", "b"]},
        }
        schema: JsonDict = {
            "oneOf": [
                {"$ref": "#/$defs/Foo"},
                {"type": "null"},
            ],
        }
        result = cast(
            JsonDict, _normalize_nullable_unions(schema, all_schemas=all_schemas)
        )
        # Falls through to oneOf -> anyOf conversion.
        assert "anyOf" in result
        assert "oneOf" not in result

    def test_multi_primitive_nullable_union_flattened(self) -> None:
        """Union with 3+ primitive branches (including null) is flattened."""
        schema: JsonDict = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        # All non-null branches are primitives: collapsed to type array.
        assert "oneOf" not in result
        assert result["type"] == ["string", "integer", "null"]

    def test_multi_branch_mixed_union_not_flattened(self) -> None:
        """Union with $ref + primitive + null stays as anyOf."""
        schema: JsonDict = {
            "oneOf": [
                {"$ref": "#/components/schemas/Foo"},
                {"type": "string"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        # Mixed branches ($ref + primitive): not flattened to type array.
        assert "anyOf" in result or "oneOf" in result
        assert not isinstance(result.get("type"), list)

    def test_discriminated_union_preserved(self) -> None:
        """oneOf without null stays oneOf."""
        schema: JsonDict = {
            "oneOf": [
                {"$ref": "#/components/schemas/TypeA"},
                {"$ref": "#/components/schemas/TypeB"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert "oneOf" in result
        assert "anyOf" not in result

    def test_jsonvalue_structural_union_becomes_anyof(self) -> None:
        """A ``JsonValue``-shaped union (object/array + primitives + null)
        converts to ``anyOf`` so no ``oneOf``-with-null survives.

        This is the shape Litestar emits for ``Mapping[str, JsonValue]``
        fields: structural branches carrying ``items`` /
        ``additionalProperties`` mixed with scalar primitives and null.
        A primitive ``type`` array cannot represent the structural
        branches, so ``anyOf`` is the only flattening that both drops the
        null-bearing ``oneOf`` and keeps the structural information.
        """
        schema: JsonDict = {
            "oneOf": [
                {"type": "array", "items": {}},
                {"type": "object", "additionalProperties": {}},
                {"type": "string"},
                {"type": "boolean"},
                {"type": "integer"},
                {"type": "number"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert "anyOf" in result
        assert "oneOf" not in result
        # The structural branches survive intact.
        assert {"type": "array", "items": {}} in result["anyOf"]
        assert {"type": "object", "additionalProperties": {}} in result["anyOf"]

    def test_constrained_primitive_union_without_structural_stays_oneof(
        self,
    ) -> None:
        """A multi-key primitive union with no structural branch stays
        ``oneOf``.

        ``{type: string, maxLength: 5} | {type: integer} | None`` escapes
        the all-single-key type-array collapse, but it carries no
        ``items`` / ``additionalProperties`` branch, so it remains a
        genuinely exclusive primitive union and must not be weakened to
        ``anyOf``.
        """
        schema: JsonDict = {
            "oneOf": [
                {"type": "string", "maxLength": 5},
                {"type": "integer"},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert "oneOf" in result
        assert "anyOf" not in result

    def test_exclusive_structural_union_stays_oneof(self) -> None:
        """An exclusive inline structural union (no scalar branch) stays
        ``oneOf``.

        ``{type: object, ...} | {type: object, ...} | None`` and an
        object+array union without scalars are genuinely exclusive (a
        value may satisfy at most one branch); they are NOT the
        ``JsonValue`` shape (which carries both ``object`` and ``array``
        plus scalars), so they must keep ``oneOf`` exclusivity.
        """
        object_only: JsonDict = {
            "oneOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(object_only))
        assert "oneOf" in result
        assert "anyOf" not in result

        object_array_only: JsonDict = {
            "oneOf": [
                {"type": "object", "additionalProperties": {}},
                {"type": "array", "items": {}},
                {"type": "null"},
            ],
        }
        result = cast(JsonDict, _normalize_nullable_unions(object_array_only))
        assert "oneOf" in result
        assert "anyOf" not in result

    def test_nested_properties_normalized(self) -> None:
        """Nullable unions inside properties are flattened."""
        schema: JsonDict = {
            "type": "object",
            "properties": {
                "deadline": {
                    "oneOf": [{"type": "string"}, {"type": "null"}],
                },
            },
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert result["properties"]["deadline"] == {
            "type": ["string", "null"],
        }

    def test_redundant_empty_schema_collapsed(self) -> None:
        """oneOf with $ref + empty {} collapses to just the $ref."""
        schema: JsonDict = {
            "items": {
                "oneOf": [
                    {"$ref": "#/components/schemas/Phase"},
                    {},
                ],
            },
            "type": "array",
        }
        result = cast(JsonDict, _normalize_nullable_unions(schema))
        assert result["items"] == {
            "$ref": "#/components/schemas/Phase",
        }

    def test_idempotent(self) -> None:
        """Running normalization twice produces the same result."""
        schema: JsonDict = {
            "oneOf": [{"type": "string"}, {"type": "null"}],
        }
        first = _normalize_nullable_unions(schema)
        second = _normalize_nullable_unions(first)
        assert first == second

    def test_full_pipeline(self) -> None:
        """Full inject_rfc9457_responses pipeline normalizes unions."""
        schema = _minimal_schema(
            extra_schemas={
                "TaskStatus": {
                    "type": "string",
                    "enum": ["pending", "done"],
                    "title": "TaskStatus",
                },
                "Task": {
                    "type": "object",
                    "properties": {
                        "assigned_to": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "null"},
                            ],
                        },
                        "status": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/TaskStatus"},
                                {"type": "null"},
                            ],
                        },
                    },
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        task = result["components"]["schemas"]["Task"]
        assert task["properties"]["assigned_to"] == {
            "type": ["string", "null"],
        }
        status = task["properties"]["status"]
        assert status["type"] == ["string", "null"]
        assert status["enum"] == ["pending", "done", None]
