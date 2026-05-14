"""A hermetic OpenAPI schema dict for generator unit tests.

A real Litestar fixture app would bring the whole app boot path
along for the ride; the generator's pure-Python render functions
only need the schema dict, so we hand-craft a minimal but
representative document that exercises every type-mapping branch:

- A clean PascalCase request DTO with ``additionalProperties: false``
  (mirrors ``model_config = ConfigDict(extra='forbid')``).
- A clean response DTO with an ``AwareDatetime`` field
  (``string`` with ``format: date-time``).
- A ``StrEnum`` schema (string + ``enum`` array) to drive the
  ``*_VALUES`` extraction in ``render_enum_values``.
- Litestar's monomorphised generic name shape
  (``ApiResponse_<inner>_``) to drive the envelope alias in
  ``render_dtos``, including the ``NoneType`` -> ``VoidEnvelope``
  special case.
- An ``allOf`` / ``oneOf`` schema name that is NOT PascalCase so
  the generator skips it (defensive).
"""

from typing import Any, Final

FIXTURE_SCHEMA: Final[dict[str, Any]] = {
    "openapi": "3.1.0",
    "info": {"title": "Fixture", "version": "0.0.1"},
    "paths": {
        "/fixture/items": {
            "post": {
                "summary": "Create",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/FixtureRequest",
                            },
                        },
                    },
                },
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/"
                                    "ApiResponse_FixtureResponse_",
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "components": {
        "schemas": {
            "FixtureEnum": {
                "type": "string",
                "enum": ["alpha", "beta", "gamma"],
                "title": "FixtureEnum",
            },
            "FixtureRequest": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "title": "Name",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "title": "Count",
                    },
                    "kind": {"$ref": "#/components/schemas/FixtureEnum"},
                },
                "required": ["name", "count", "kind"],
                "title": "FixtureRequest",
            },
            "FixtureResponse": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "title": "Id"},
                    "created_at": {
                        "type": "string",
                        "format": "date-time",
                        "title": "CreatedAt",
                    },
                    "status": {"$ref": "#/components/schemas/FixtureEnum"},
                },
                "required": ["id", "created_at", "status"],
                "title": "FixtureResponse",
            },
            "ApiResponse_FixtureResponse_": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "data": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/FixtureResponse"},
                            {"type": "null"},
                        ],
                        "default": None,
                    },
                    "error": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                    },
                },
                "title": "ApiResponse[FixtureResponse]",
            },
            "ApiResponse_NoneType_": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "data": {"type": "null", "default": None},
                    "error": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                    },
                },
                "title": "ApiResponse[NoneType]",
            },
            "PaginatedResponse_FixtureResponse_": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {
                            "$ref": "#/components/schemas/FixtureResponse",
                        },
                        "default": [],
                    },
                },
                "title": "PaginatedResponse[FixtureResponse]",
            },
            # Defensive: an inline schema that is NOT a real Pydantic
            # class name. The generator must skip both the alias layer
            # and the enum-values walk for it.
            "inline_anon_schema": {
                "type": "string",
                "enum": ["x", "y"],
            },
        },
    },
}
