"""Tests for nullable union normalization in OpenAPI schema post-processing.

Verifies that :func:`_normalize_nullable_unions` correctly flattens
``oneOf``/``anyOf`` nullable unions to JSON Schema 2020-12 ``type``
arrays, inlines enum ``$ref`` targets, and collapses redundant unions.
"""

from typing import Any

import pytest

from synthorg.api.openapi import inject_rfc9457_responses
from synthorg.api.openapi_normalize import _normalize_nullable_unions


def _minimal_schema(
    *,
    extra_schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenAPI schema dict for normalization tests."""
    schemas: dict[str, Any] = {
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
        schema: dict[str, Any] = {
            "oneOf": [{"type": "string"}, {"type": "null"}],
        }
        result: Any = _normalize_nullable_unions(schema)
        assert result == {"type": ["string", "null"]}

    def test_primitive_anyof_flattened(self) -> None:
        """anyOf with primitive + null becomes type array."""
        schema: dict[str, Any] = {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
        }
        result: Any = _normalize_nullable_unions(schema)
        assert result == {"type": ["integer", "null"]}

    def test_constraints_preserved(self) -> None:
        """Extra properties (minLength, format) are kept."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string", "format": "date-time"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
        assert result == {"type": ["string", "null"], "format": "date-time"}

    def test_enum_ref_inlined(self) -> None:
        """$ref to enum + null inlines enum values and flattens."""
        all_schemas: dict[str, Any] = {
            "Status": {
                "type": "string",
                "enum": ["active", "inactive"],
                "title": "Status",
            },
        }
        schema: dict[str, Any] = {
            "description": "Current status",
            "oneOf": [
                {"$ref": "#/components/schemas/Status"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema, all_schemas=all_schemas)
        assert result["type"] == ["string", "null"]
        assert result["enum"] == ["active", "inactive", None]
        assert result["description"] == "Current status"

    def test_enum_ref_without_description(self) -> None:
        """$ref to enum + null without description omits description key."""
        all_schemas: dict[str, Any] = {
            "Status": {
                "type": "string",
                "enum": ["on", "off"],
                "title": "Status",
            },
        }
        schema: dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/components/schemas/Status"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema, all_schemas=all_schemas)
        assert result["type"] == ["string", "null"]
        assert "description" not in result

    def test_object_ref_becomes_anyof(self) -> None:
        """$ref to object + null uses anyOf (known renderer limitation)."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/components/schemas/Minutes"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
        assert "anyOf" in result
        assert "oneOf" not in result

    def test_object_ref_anyof_with_registry_stays_anyof(self) -> None:
        """anyOf with non-enum $ref + null stays anyOf when registry provided."""
        all_schemas: dict[str, Any] = {
            "Minutes": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        }
        schema: dict[str, Any] = {
            "anyOf": [
                {"$ref": "#/components/schemas/Minutes"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema, all_schemas=all_schemas)
        assert "anyOf" in result
        assert len(result["anyOf"]) == 2

    def test_ref_with_non_component_prefix_not_inlined(self) -> None:
        """$ref with non-#/components/schemas/ prefix falls through."""
        all_schemas: dict[str, Any] = {
            "Foo": {"type": "string", "enum": ["a", "b"]},
        }
        schema: dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/$defs/Foo"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema, all_schemas=all_schemas)
        # Falls through to oneOf -> anyOf conversion.
        assert "anyOf" in result
        assert "oneOf" not in result

    def test_multi_primitive_nullable_union_flattened(self) -> None:
        """Union with 3+ primitive branches (including null) is flattened."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
        # All non-null branches are primitives: collapsed to type array.
        assert "oneOf" not in result
        assert result["type"] == ["string", "integer", "null"]

    def test_multi_branch_mixed_union_not_flattened(self) -> None:
        """Union with $ref + primitive + null stays as anyOf."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/components/schemas/Foo"},
                {"type": "string"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
        # Mixed branches ($ref + primitive): not flattened to type array.
        assert "anyOf" in result or "oneOf" in result
        assert not isinstance(result.get("type"), list)

    def test_discriminated_union_preserved(self) -> None:
        """oneOf without null stays oneOf."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/components/schemas/TypeA"},
                {"$ref": "#/components/schemas/TypeB"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
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
        schema: dict[str, Any] = {
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
        result: Any = _normalize_nullable_unions(schema)
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
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string", "maxLength": 5},
                {"type": "integer"},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(schema)
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
        object_only: dict[str, Any] = {
            "oneOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
                {"type": "null"},
            ],
        }
        result: Any = _normalize_nullable_unions(object_only)
        assert "oneOf" in result
        assert "anyOf" not in result

        object_array_only: dict[str, Any] = {
            "oneOf": [
                {"type": "object", "additionalProperties": {}},
                {"type": "array", "items": {}},
                {"type": "null"},
            ],
        }
        result = _normalize_nullable_unions(object_array_only)
        assert "oneOf" in result
        assert "anyOf" not in result

    def test_nested_properties_normalized(self) -> None:
        """Nullable unions inside properties are flattened."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "deadline": {
                    "oneOf": [{"type": "string"}, {"type": "null"}],
                },
            },
        }
        result: Any = _normalize_nullable_unions(schema)
        assert result["properties"]["deadline"] == {
            "type": ["string", "null"],
        }

    def test_redundant_empty_schema_collapsed(self) -> None:
        """oneOf with $ref + empty {} collapses to just the $ref."""
        schema: dict[str, Any] = {
            "items": {
                "oneOf": [
                    {"$ref": "#/components/schemas/Phase"},
                    {},
                ],
            },
            "type": "array",
        }
        result: Any = _normalize_nullable_unions(schema)
        assert result["items"] == {
            "$ref": "#/components/schemas/Phase",
        }

    def test_idempotent(self) -> None:
        """Running normalization twice produces the same result."""
        schema: dict[str, Any] = {
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
        result: dict[str, Any] = inject_rfc9457_responses(schema)
        task = result["components"]["schemas"]["Task"]
        assert task["properties"]["assigned_to"] == {
            "type": ["string", "null"],
        }
        status = task["properties"]["status"]
        assert status["type"] == ["string", "null"]
        assert status["enum"] == ["pending", "done", None]
