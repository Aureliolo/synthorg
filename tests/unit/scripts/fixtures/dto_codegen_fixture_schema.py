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
- A request-only schema, a response-only schema, and a both-sided
  schema, each with a defaulted property absent from ``required[]``,
  to drive ``_promote_response_defaults_to_required``.
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
        "/fixture/promotion": {
            "post": {
                "summary": "Promotion fixture",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/"
                                "FixtureRequestWithDefault",
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
                                    "FixtureResponseWithDefault",
                                },
                            },
                        },
                    },
                },
            },
            "put": {
                "summary": "Both-sided fixture",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/FixtureBothSided",
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
                                    "$ref": "#/components/schemas/FixtureBothSided",
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
            # Reached only via a ``requestBody.$ref`` -- the promoter
            # must leave its defaulted properties alone so request
            # types stay optional client-side.
            "FixtureRequestWithDefault": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "required_field": {
                        "type": "string",
                        "title": "RequiredField",
                    },
                    "optional_with_default": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "OptionalWithDefault",
                    },
                    "optional_no_default": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "title": "OptionalNoDefault",
                    },
                },
                "required": ["required_field"],
                "title": "FixtureRequestWithDefault",
            },
            # Reached only via a response $ref -- the promoter must
            # move ``optional_with_default`` into ``required[]``.
            "FixtureResponseWithDefault": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "required_field": {
                        "type": "string",
                        "title": "RequiredField",
                    },
                    "optional_with_default": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "OptionalWithDefault",
                    },
                    "optional_no_default": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "title": "OptionalNoDefault",
                    },
                },
                "required": ["required_field"],
                "title": "FixtureResponseWithDefault",
            },
            # Reached via BOTH a requestBody $ref and a response $ref.
            # The promoter treats both-sided schemas as response-side
            # (response wins) so the defaulted property is promoted.
            "FixtureBothSided": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "optional_with_default": {
                        "type": "string",
                        "default": "",
                        "title": "OptionalWithDefault",
                    },
                },
                "title": "FixtureBothSided",
            },
        },
    },
}
