"""Tests for RFC 9457 OpenAPI schema post-processing.

Verifies that :func:`inject_rfc9457_responses` correctly adds
``ProblemDetail`` schema, reusable error responses with dual
content types, and injects error references into operations.
"""

import copy

import pytest

from synthorg.api.openapi import inject_rfc9457_responses
from synthorg.api.openapi_responses import _should_inject
from tests._shared import JsonDict

# ── Fixtures ──────────────────────────────────────────────────


def _minimal_operation(
    *,
    status: int = 200,
    has_body: bool = False,
) -> JsonDict:
    """Build a minimal OpenAPI operation dict."""
    op: JsonDict = {
        "responses": {
            str(status): {
                "description": "OK",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
        },
    }
    if has_body:
        op["requestBody"] = {
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    return op


def _minimal_schema(
    *,
    paths: dict[str, JsonDict] | None = None,
    extra_schemas: JsonDict | None = None,
) -> JsonDict:
    """Build a minimal OpenAPI schema dict for testing."""
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
        "paths": paths or {},
        "components": {"schemas": schemas},
    }


@pytest.fixture
def base_schema() -> JsonDict:
    """Schema with representative paths for injection testing."""
    return _minimal_schema(
        paths={
            "/api/v1/healthz": {
                "get": _minimal_operation(),
            },
            "/api/v1/auth/login": {
                "post": _minimal_operation(status=200, has_body=True),
            },
            "/api/v1/auth/setup": {
                "post": _minimal_operation(status=201, has_body=True),
            },
            "/api/v1/tasks": {
                "get": _minimal_operation(),
                "post": _minimal_operation(status=201, has_body=True),
            },
            "/api/v1/tasks/{task_id}": {
                "get": _minimal_operation(),
                "delete": _minimal_operation(),
                "patch": _minimal_operation(has_body=True),
            },
            "/api/v1/agents": {
                "get": _minimal_operation(),
            },
            "/api/v1/agents/{agent_name}": {
                "get": _minimal_operation(),
            },
        },
    )


# ── ProblemDetail schema ─────────────────────────────────────


@pytest.mark.unit
class TestProblemDetailSchema:
    """ProblemDetail schema is correctly added to components."""

    def test_added_to_components(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        schemas = result["components"]["schemas"]
        assert "ProblemDetail" in schemas

    def test_has_required_fields(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pd = result["components"]["schemas"]["ProblemDetail"]
        required = set(pd.get("required", []))
        expected = {
            "type",
            "title",
            "status",
            "detail",
            "instance",
            "error_code",
            "error_category",
        }
        assert expected == required

    def test_has_all_properties(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pd = result["components"]["schemas"]["ProblemDetail"]
        props = set(pd.get("properties", {}).keys())
        expected = {
            "type",
            "title",
            "status",
            "detail",
            "instance",
            "error_code",
            "error_category",
            "retryable",
            "retry_after",
        }
        assert expected == props

    def test_reuses_existing_error_code_ref(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pd = result["components"]["schemas"]["ProblemDetail"]
        error_code_prop = pd["properties"]["error_code"]
        assert error_code_prop == {"$ref": "#/components/schemas/ErrorCode"}

    def test_reuses_existing_error_category_ref(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pd = result["components"]["schemas"]["ProblemDetail"]
        error_cat_prop = pd["properties"]["error_category"]
        assert error_cat_prop == {
            "$ref": "#/components/schemas/ErrorCategory",
        }

    def test_not_overwritten_if_exists(self) -> None:
        """Pre-existing ProblemDetail schema is preserved."""
        custom_pd = {"type": "object", "custom": True}
        schema = _minimal_schema(extra_schemas={"ProblemDetail": custom_pd})
        result: JsonDict = inject_rfc9457_responses(schema)
        assert result["components"]["schemas"]["ProblemDetail"]["custom"] is True

    def test_no_defs_in_problem_detail(self, base_schema: JsonDict) -> None:
        """ProblemDetail schema has no leftover $defs."""
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pd = result["components"]["schemas"]["ProblemDetail"]
        assert "$defs" not in pd


# ── Reusable responses ────────────────────────────────────────

_EXPECTED_RESPONSE_KEYS = frozenset(
    {
        "BadRequest",
        "Unauthorized",
        "Forbidden",
        "NotFound",
        "Conflict",
        "TooManyRequests",
        "InternalError",
        "ServiceUnavailable",
    }
)

_EXPECTED_STATUS_CODES: dict[str, int] = {
    "BadRequest": 400,
    "Unauthorized": 401,
    "Forbidden": 403,
    "NotFound": 404,
    "Conflict": 409,
    "TooManyRequests": 429,
    "InternalError": 500,
    "ServiceUnavailable": 503,
}


@pytest.mark.unit
class TestReusableResponses:
    """Reusable responses are defined with dual content types."""

    def test_all_responses_defined(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        responses = result["components"]["responses"]
        assert set(responses.keys()) == _EXPECTED_RESPONSE_KEYS

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_has_dual_content_types(self, base_schema: JsonDict, key: str) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        content = result["components"]["responses"][key]["content"]
        assert "application/json" in content
        assert "application/problem+json" in content

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_json_content_refs_envelope(self, base_schema: JsonDict, key: str) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        json_content = result["components"]["responses"][key]["content"][
            "application/json"
        ]
        assert json_content["schema"] == {
            "$ref": "#/components/schemas/ApiResponse_NoneType_"
        }

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_problem_json_refs_problem_detail(
        self, base_schema: JsonDict, key: str
    ) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        pj_content = result["components"]["responses"][key]["content"][
            "application/problem+json"
        ]
        assert pj_content["schema"] == {
            "$ref": "#/components/schemas/ProblemDetail",
        }

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_examples_present(self, base_schema: JsonDict, key: str) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        content = result["components"]["responses"][key]["content"]
        # Both content types must have an example.
        assert "example" in content["application/json"]
        assert "example" in content["application/problem+json"]

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_envelope_example_structure(self, base_schema: JsonDict, key: str) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        example = result["components"]["responses"][key]["content"]["application/json"][
            "example"
        ]
        assert example["data"] is None
        assert example["success"] is False
        assert isinstance(example["error"], str)
        assert isinstance(example["error_detail"], dict)
        detail = example["error_detail"]
        assert "error_code" in detail
        assert "error_category" in detail
        assert "instance" in detail

    @pytest.mark.parametrize("key", sorted(_EXPECTED_RESPONSE_KEYS))
    def test_problem_detail_example_structure(
        self, base_schema: JsonDict, key: str
    ) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        example = result["components"]["responses"][key]["content"][
            "application/problem+json"
        ]["example"]
        assert "type" in example
        assert "title" in example
        assert "status" in example
        assert isinstance(example["status"], int)
        assert "detail" in example
        assert "instance" in example
        assert "error_code" in example
        assert "error_category" in example

    @pytest.mark.parametrize(
        ("key", "expected_status"),
        sorted(_EXPECTED_STATUS_CODES.items()),
    )
    def test_problem_detail_example_status_matches_http_code(
        self,
        base_schema: JsonDict,
        key: str,
        expected_status: int,
    ) -> None:
        """ProblemDetail example status matches the HTTP status code."""
        result: JsonDict = inject_rfc9457_responses(base_schema)
        example = result["components"]["responses"][key]["content"][
            "application/problem+json"
        ]["example"]
        assert example["status"] == expected_status


# ── Operation injection ───────────────────────────────────────


@pytest.mark.unit
class TestOperationInjection:
    """Error responses are injected into the correct operations."""

    def test_all_operations_have_500(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        for path, path_item in result["paths"].items():
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses", {})
                assert "500" in responses, f"{method.upper()} {path} missing 500"
                assert responses["500"] == {
                    "$ref": "#/components/responses/InternalError"
                }

    def test_authenticated_endpoints_have_401(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        # Non-public paths should have 401.
        for path in ("/api/v1/tasks", "/api/v1/agents"):
            for method in result["paths"][path]:
                responses = result["paths"][path][method]["responses"]
                assert "401" in responses, f"{method.upper()} {path} missing 401"

    def test_public_endpoints_skip_401(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        for path in (
            "/api/v1/healthz",
            "/api/v1/auth/login",
            "/api/v1/auth/setup",
        ):
            for method in result["paths"][path]:
                op = result["paths"][path][method]
                if not isinstance(op, dict):
                    continue
                responses = op.get("responses", {})
                assert "401" not in responses, f"{path} should not have 401"
                assert "403" not in responses, f"{path} should not have 403"

    def test_path_param_endpoints_have_404(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        for path in (
            "/api/v1/tasks/{task_id}",
            "/api/v1/agents/{agent_name}",
        ):
            for method in result["paths"][path]:
                responses = result["paths"][path][method]["responses"]
                assert "404" in responses, f"{method.upper()} {path} missing 404"

    def test_non_param_endpoints_skip_404(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        for path in ("/api/v1/tasks", "/api/v1/agents"):
            for method in result["paths"][path]:
                responses = result["paths"][path][method]["responses"]
                assert "404" not in responses, (
                    f"{method.upper()} {path} should not have 404"
                )

    def test_write_endpoints_have_409(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        # POST /tasks should have 409
        post_responses = result["paths"]["/api/v1/tasks"]["post"]["responses"]
        assert "409" in post_responses
        # PATCH should also have 409 (update can conflict).
        patch_responses = result["paths"]["/api/v1/tasks/{task_id}"]["patch"][
            "responses"
        ]
        assert "409" in patch_responses

    def test_get_endpoints_skip_409(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        get_responses = result["paths"]["/api/v1/tasks"]["get"]["responses"]
        assert "409" not in get_responses

    def test_write_endpoints_have_403(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        post_responses = result["paths"]["/api/v1/tasks"]["post"]["responses"]
        assert "403" in post_responses

    def test_authenticated_get_endpoints_have_403(self, base_schema: JsonDict) -> None:
        # Authenticated read endpoints sit behind a role/permission guard
        # (e.g. require_read_access), so 403 is reachable on GET too.
        result: JsonDict = inject_rfc9457_responses(base_schema)
        get_responses = result["paths"]["/api/v1/tasks"]["get"]["responses"]
        assert "403" in get_responses

    def test_existing_responses_not_overwritten(self) -> None:
        """Pre-existing non-400 error responses are preserved."""
        custom_404 = {"description": "Custom not found", "custom": True}
        schema = _minimal_schema(
            paths={
                "/api/v1/items/{id}": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK"},
                            "404": custom_404,
                        },
                    },
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        resp_404 = result["paths"]["/api/v1/items/{id}"]["get"]["responses"]["404"]
        # Should keep the custom response, not overwrite with ref.
        assert resp_404.get("custom") is True

    def test_default_400_replaced(self) -> None:
        """Litestar's default 400 (ValidationException) is replaced."""
        litestar_400 = {
            "description": "Bad request",
            "content": {
                "application/json": {
                    "schema": {
                        "properties": {
                            "status_code": {"type": "integer"},
                            "detail": {"type": "string"},
                            "extra": {"type": ["null", "object"]},
                        },
                        "description": "Validation Exception",
                    },
                },
            },
        }
        schema = _minimal_schema(
            paths={
                "/api/v1/tasks": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK"},
                            "400": litestar_400,
                        },
                    },
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        resp_400 = result["paths"]["/api/v1/tasks"]["get"]["responses"]["400"]
        # Should be replaced with our ref.
        assert resp_400 == {"$ref": "#/components/responses/BadRequest"}

    def test_custom_400_preserved(self) -> None:
        """Custom (non-Litestar) 400 response is not replaced."""
        custom_400 = {
            "description": "Custom validation",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": "Custom error schema",
                    },
                },
            },
        }
        schema = _minimal_schema(
            paths={
                "/api/v1/tasks": {
                    "post": {
                        "responses": {
                            "201": {"description": "Created"},
                            "400": custom_400,
                        },
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"},
                                },
                            },
                        },
                    },
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        resp_400 = result["paths"]["/api/v1/tasks"]["post"]["responses"]["400"]
        # Custom 400 should be preserved (not Litestar's ValidationException).
        assert resp_400["description"] == "Custom validation"

    def test_non_public_have_429(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        responses = result["paths"]["/api/v1/tasks"]["get"]["responses"]
        assert "429" in responses

    def test_public_skip_429(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        responses = result["paths"]["/api/v1/healthz"]["get"]["responses"]
        assert "429" not in responses

    def test_public_skip_503(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        responses = result["paths"]["/api/v1/healthz"]["get"]["responses"]
        assert "503" not in responses

    def test_delete_skip_409(self, base_schema: JsonDict) -> None:
        """DELETE is idempotent -- conflicts are a create/update concern."""
        result: JsonDict = inject_rfc9457_responses(base_schema)
        responses = result["paths"]["/api/v1/tasks/{task_id}"]["delete"]["responses"]
        assert "409" not in responses

    @pytest.mark.parametrize("key", ["TooManyRequests", "ServiceUnavailable"])
    def test_retryable_example_has_retryable_true(
        self, base_schema: JsonDict, key: str
    ) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        content = result["components"]["responses"][key]["content"]
        envelope_ex = content["application/json"]["example"]
        assert envelope_ex["error_detail"]["retryable"] is True
        problem_ex = content["application/problem+json"]["example"]
        assert problem_ex["retryable"] is True

    @pytest.mark.parametrize(
        "key",
        [
            "BadRequest",
            "Unauthorized",
            "Forbidden",
            "NotFound",
            "Conflict",
            "InternalError",
        ],
    )
    def test_non_retryable_example_has_retryable_false(
        self, base_schema: JsonDict, key: str
    ) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        content = result["components"]["responses"][key]["content"]
        envelope_ex = content["application/json"]["example"]
        assert envelope_ex["error_detail"]["retryable"] is False
        problem_ex = content["application/problem+json"]["example"]
        assert problem_ex["retryable"] is False

    def test_put_endpoints_have_write_responses(self) -> None:
        """PUT method gets 400, 403, and 409 injected."""
        schema = _minimal_schema(
            paths={
                "/api/v1/tasks/{task_id}": {
                    "put": _minimal_operation(has_body=True),
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        responses = result["paths"]["/api/v1/tasks/{task_id}"]["put"]["responses"]
        assert "400" in responses
        assert "403" in responses
        assert "409" in responses

    def test_get_without_existing_400_skips_bad_request(
        self, base_schema: JsonDict
    ) -> None:
        """GET endpoint without pre-existing 400 does not get BadRequest."""
        result: JsonDict = inject_rfc9457_responses(base_schema)
        # GET /tasks has no pre-existing 400 and is not a write method.
        responses = result["paths"]["/api/v1/tasks"]["get"]["responses"]
        assert "400" not in responses

    def test_skips_non_operation_entries(self) -> None:
        """Path-level metadata (parameters) is not treated as operations."""
        schema = _minimal_schema(
            paths={
                "/api/v1/items/{id}": {
                    "parameters": [{"name": "id", "in": "path"}],
                    "get": _minimal_operation(),
                },
            },
        )
        result: JsonDict = inject_rfc9457_responses(schema)
        # Should not crash; GET should have 500 injected.
        responses = result["paths"]["/api/v1/items/{id}"]["get"]["responses"]
        assert "500" in responses
        # Parameters list should be unchanged.
        params = result["paths"]["/api/v1/items/{id}"]["parameters"]
        assert isinstance(params, list)

    def test_unknown_key_returns_false(self) -> None:
        """Unknown response key falls back to False (not injected)."""
        result = _should_inject(
            key="UnknownResponse",
            path="/api/v1/tasks",
            method="get",
            operation={"responses": {"200": {"description": "OK"}}},
        )
        assert result is False


# ── Info description ──────────────────────────────────────────


@pytest.mark.unit
class TestInfoDescription:
    """RFC 9457 documentation is stored in x-documentation extension."""

    def test_rfc9457_in_x_documentation(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        xdoc = result["info"]["x-documentation"]
        assert "rfc9457" in xdoc
        assert "RFC 9457" in xdoc["rfc9457"]

    def test_not_in_description(self, base_schema: JsonDict) -> None:
        """RFC 9457 docs should not pollute info.description."""
        result: JsonDict = inject_rfc9457_responses(base_schema)
        desc = result["info"].get("description", "")
        assert "RFC 9457" not in desc

    def test_mentions_content_negotiation(self, base_schema: JsonDict) -> None:
        result: JsonDict = inject_rfc9457_responses(base_schema)
        rfc_doc = result["info"]["x-documentation"]["rfc9457"]
        assert "application/problem+json" in rfc_doc
        assert "application/json" in rfc_doc

    def test_preserves_existing_description(self) -> None:
        """Existing info.description is not modified."""
        schema = _minimal_schema()
        schema["info"]["description"] = "My custom API description."
        result: JsonDict = inject_rfc9457_responses(schema)
        assert result["info"]["description"] == "My custom API description."


# ── Idempotency and immutability ──────────────────────────────


@pytest.mark.unit
class TestIdempotencyAndImmutability:
    """Function is idempotent and does not mutate the input."""

    def test_idempotent(self, base_schema: JsonDict) -> None:
        first = inject_rfc9457_responses(base_schema)
        second = inject_rfc9457_responses(first)
        assert first == second

    def test_does_not_mutate_input(self, base_schema: JsonDict) -> None:
        original = copy.deepcopy(base_schema)
        inject_rfc9457_responses(base_schema)
        assert base_schema == original

    def test_empty_paths(self) -> None:
        """Handles schema with no paths gracefully."""
        schema = _minimal_schema(paths={})
        result: JsonDict = inject_rfc9457_responses(schema)
        assert "ProblemDetail" in result["components"]["schemas"]
        assert len(result["components"]["responses"]) == 8

    def test_missing_components(self) -> None:
        """Handles schema with missing components section."""
        schema: JsonDict = {
            "openapi": "3.1.0",
            "info": {"title": "X", "version": "1"},
        }
        result: JsonDict = inject_rfc9457_responses(schema)
        assert "ProblemDetail" in result["components"]["schemas"]
