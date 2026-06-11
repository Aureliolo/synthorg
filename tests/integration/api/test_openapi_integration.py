"""Integration test for RFC 9457 OpenAPI schema post-processing.

Exercises :func:`inject_rfc9457_responses` against the real
Litestar-generated schema end-to-end.
"""

import pytest

from synthorg.api.openapi import inject_rfc9457_responses
from tests._shared import JsonDict

# Mirrors ``scripts/check_openapi_liveness.py``.  Duplicated (not imported)
# so the test is self-contained and the canary set can evolve
# independently of the CI gate if needed.
_MIN_PATH_COUNT = 200
_CANARY_PATHS = frozenset(
    {
        "/api/v1/healthz",
        "/api/v1/readyz",
        "/api/v1/agents",
        "/api/v1/clients",
        "/api/v1/workflows",
        "/api/v1/budget/config",
        "/api/v1/departments",
    }
)

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


@pytest.mark.integration
def test_full_app_schema_enhancement() -> None:
    """Enhance the real Litestar-generated schema end-to-end."""
    from synthorg.api.app import create_app

    app = create_app()
    schema: JsonDict = app.openapi_schema.to_schema()
    result: JsonDict = inject_rfc9457_responses(schema)

    # ProblemDetail schema present.
    assert "ProblemDetail" in result["components"]["schemas"]

    # All 8 reusable responses defined (subset check -- schema may
    # contain additional non-RFC-9457 reusable responses).
    responses = result["components"]["responses"]
    assert _EXPECTED_RESPONSE_KEYS.issubset(responses.keys())

    # Every RFC 9457 response has dual content types.
    for key in _EXPECTED_RESPONSE_KEYS:
        resp = responses[key]
        content = resp["content"]
        assert "application/json" in content, f"{key} missing application/json"
        assert "application/problem+json" in content, f"{key} missing problem+json"

    # At least one operation has error response refs.
    tasks_get = result["paths"]["/api/v1/tasks"]["get"]["responses"]
    assert "500" in tasks_get
    assert tasks_get["500"] == {
        "$ref": "#/components/responses/InternalError",
    }

    # Public endpoints don't have 401.
    healthz = result["paths"]["/api/v1/healthz"]["get"]["responses"]
    readyz = result["paths"]["/api/v1/readyz"]["get"]["responses"]
    assert "401" not in healthz
    assert "401" not in readyz

    # RFC 9457 docs in x-documentation, not info.description.
    assert "RFC 9457" not in result["info"].get("description", "")
    assert "rfc9457" in result["info"]["x-documentation"]


def _find_oneof_with_null(
    obj: object,
    path: str = "$",
) -> list[str]:
    """Find all ``oneOf`` arrays containing a null type."""
    violations: list[str] = []
    if isinstance(obj, dict):
        if "oneOf" in obj and isinstance(obj["oneOf"], list):
            for item in obj["oneOf"]:
                if isinstance(item, dict) and item.get("type") == "null":
                    violations.append(path)
                    break
        for key, value in obj.items():
            violations.extend(_find_oneof_with_null(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            violations.extend(_find_oneof_with_null(item, f"{path}[{i}]"))
    return violations


@pytest.mark.integration
def test_no_oneof_with_null_after_processing() -> None:
    """No ``oneOf``-with-null survives post-processing.

    Catches regressions when new models with optional fields are
    added.
    """
    from synthorg.api.app import create_app

    app = create_app()
    schema: JsonDict = app.openapi_schema.to_schema()
    result: JsonDict = inject_rfc9457_responses(schema)

    violations = _find_oneof_with_null(result)
    assert violations == [], (
        f"oneOf-with-null found after post-processing: {violations}"
    )


@pytest.mark.integration
def test_openapi_export_is_live_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export produces a full schema under the CI determinism contract.

    Mirrors ``scripts/check_openapi_liveness.py``: guards against the
    ``SYNTHORG_DB_PATH``-unset regression.  If a future change removes the
    ``setdefault`` in ``scripts/export_openapi.py`` or breaks wiring so
    controllers silently skip registration, the export still succeeds
    but the schema is partial; this test fails loudly in that case.
    """
    monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
    monkeypatch.delenv("SYNTHORG_DATABASE_URL", raising=False)

    from synthorg.api.app import create_app

    app = create_app()
    schema: JsonDict = inject_rfc9457_responses(app.openapi_schema.to_schema())
    paths = schema.get("paths") or {}

    assert len(paths) >= _MIN_PATH_COUNT, (
        f"Schema has {len(paths)} paths; expected at least {_MIN_PATH_COUNT}. "
        "Check SYNTHORG_DB_PATH handling in scripts/export_openapi.py "
        "and any controller wiring that depends on persistence."
    )

    missing_canary = sorted(_CANARY_PATHS - paths.keys())
    assert not missing_canary, (
        f"Canary endpoints missing from schema: {missing_canary}. "
        "Either the route was removed (update _CANARY_PATHS) or wiring "
        "regressed (fix the underlying controller)."
    )


_EXPECTED_AUTH_PATHS = frozenset(
    {
        "/api/v1/auth/setup",
        "/api/v1/auth/login",
        "/api/v1/auth/change-password",
        "/api/v1/auth/me",
        "/api/v1/auth/ws-ticket",
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/{session_id}",
        "/api/v1/auth/logout",
    }
)


@pytest.mark.integration
def test_no_doubled_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No route may register at a doubled /api/v1/api/v1/* path.

    The root ``Router(path=api_config.api_prefix)`` in ``app.py`` already
    mounts controllers under ``/api/v1``.  Any controller that ALSO
    hardcodes ``/api/v1`` in its own ``path = "..."`` declaration ends
    up doubled.  This test guards the invariant going forward.
    """
    monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
    monkeypatch.delenv("SYNTHORG_DATABASE_URL", raising=False)

    from synthorg.api.app import create_app

    app = create_app()
    paths = app.openapi_schema.to_schema().get("paths") or {}
    doubled = sorted(p for p in paths if p.startswith("/api/v1/api/v1"))
    assert not doubled, (
        f"Doubled /api/v1 prefix detected in {len(doubled)} route(s): "
        f"{doubled}.  The owning controller hardcodes /api/v1 in its "
        "``path = '...'`` declaration -- strip that leading ``/api/v1`` "
        "and let the root Router prefix own it."
    )


@pytest.mark.integration
def test_auth_endpoints_in_openapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All /auth/* endpoints from ``AuthController`` must appear in the
    exported schema.

    ``AuthController`` is wired into ``BASE_CONTROLLERS`` and registers
    8 endpoints.  A regression that silently strips them from the
    schema -- via an accidental ``include_in_schema=False``, a broken
    guard, or controller-level exclusion -- would break the generated
    API docs and any client SDKs derived from the spec.
    """
    monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
    monkeypatch.delenv("SYNTHORG_DATABASE_URL", raising=False)

    from synthorg.api.app import create_app

    app = create_app()
    paths = app.openapi_schema.to_schema().get("paths") or {}
    missing = sorted(_EXPECTED_AUTH_PATHS - paths.keys())
    assert not missing, (
        f"Auth endpoints missing from OpenAPI schema: {missing}.  "
        "Check AuthController in src/synthorg/api/auth/controller.py "
        "for ``include_in_schema=False`` or a guard that prevents "
        "schema traversal."
    )
