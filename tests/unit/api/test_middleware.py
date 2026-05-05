"""Tests for request middleware and security headers hook."""

from typing import Any

import pytest
from litestar import Litestar, get, post
from litestar.enums import ScopeType
from litestar.exceptions import ValidationException
from litestar.testing import TestClient

from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.middleware import (
    _API_CACHE_CONTROL,
    _API_CSP,
    _DOCS_CACHE_CONTROL,
    _DOCS_CSP,
    _DOCS_CSP_DEFAULT_ORIGINS,
    _SECURITY_HEADERS,
    RequestLoggingMiddleware,
    build_docs_csp,
    security_headers_hook,
    set_docs_csp_origins,
)

pytestmark = pytest.mark.unit


def _make_app(*handlers: Any) -> Litestar:
    """Build a minimal Litestar app with the security hook wired in."""
    return Litestar(
        route_handlers=list(handlers),
        before_send=[security_headers_hook],
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )


def _assert_all_security_headers(
    resp: Any,
    *,
    status: int,
) -> None:
    """Assert that all static security headers and CSP are present.

    Only valid for non-docs paths (where COOP is ``same-origin``
    and CSP is the strict API policy).
    """
    for name, expected in _SECURITY_HEADERS.items():
        assert resp.headers.get(name) == expected, (
            f"Missing or wrong header on {status}: {name}"
        )
    # CSP must also be present (strict for non-docs paths).
    assert resp.headers.get("content-security-policy") == _API_CSP, (
        f"Missing or wrong CSP on {status}"
    )


# ── Security headers hook ──────────────────────────────────────


class TestSecurityHeadersHook:
    """Verify security headers appear on ALL response types."""

    def test_success_response_has_all_security_headers(self) -> None:
        """200 OK carries every static security header and CSP."""

        @get("/ok")
        async def handler() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/ok")
            assert resp.status_code == 200
            _assert_all_security_headers(resp, status=200)

    def test_exception_handler_response_has_security_headers(
        self,
    ) -> None:
        """Exception-handler 400 carries all security headers."""

        @get("/fail")
        async def handler() -> None:
            raise ValidationException

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/fail")
            assert resp.status_code == 400
            _assert_all_security_headers(resp, status=400)

    def test_unmatched_route_404_has_security_headers(self) -> None:
        """Router-level 404 (no matching route) carries headers."""

        @get("/exists")
        async def handler() -> str:
            return "ok"

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/nonexistent")
            assert resp.status_code == 404
            _assert_all_security_headers(resp, status=404)

    def test_method_not_allowed_405_has_security_headers(self) -> None:
        """Router-level 405 carries security headers."""

        @post("/only-post")
        async def handler() -> str:
            return "ok"

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/only-post")
            assert resp.status_code == 405
            _assert_all_security_headers(resp, status=405)

    def test_500_error_has_security_headers(self) -> None:
        """Unexpected error 500 carries security headers."""

        @get("/boom")
        async def handler() -> None:
            msg = "unexpected"
            raise RuntimeError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/boom")
            assert resp.status_code == 500
            _assert_all_security_headers(resp, status=500)

    async def test_non_http_scope_is_skipped(self) -> None:
        """Non-HTTP scopes (WebSocket, lifespan) are not modified."""
        message: Any = {
            "type": "websocket.connect",
            "headers": [],
        }
        scope: Any = {"type": ScopeType.WEBSOCKET}

        await security_headers_hook(message, scope)

        # Headers list should remain empty -- hook returned early.
        assert message.get("headers") == []


# ── CSP path selection ─────────────────────────────────────────


class TestCSPPathSelection:
    """Verify path-aware CSP via the before_send hook."""

    @pytest.mark.parametrize(
        ("path", "expected_csp"),
        [
            ("/api/v1/healthz", _API_CSP),
            ("/documents", _API_CSP),
            ("/docsearch", _API_CSP),
            ("/docs", _DOCS_CSP),
            ("/docs/api", _DOCS_CSP),
            ("/docs/openapi.json", _DOCS_CSP),
        ],
        ids=[
            "api-strict",
            "documents-strict",
            "docsearch-strict",
            "docs-exact-relaxed",
            "docs-subpath-relaxed",
            "docs-openapi-relaxed",
        ],
    )
    def test_csp_path_selection(
        self,
        test_client: TestClient[Any],
        path: str,
        expected_csp: str,
    ) -> None:
        """Verify CSP assignment: strict for API, relaxed for /docs."""
        response = test_client.get(path)
        csp = response.headers.get("content-security-policy")
        assert csp == expected_csp

    def test_docs_path_relaxes_coop(self, test_client: TestClient[Any]) -> None:
        """Docs paths get COOP same-origin-allow-popups for Scalar UI."""
        response = test_client.get("/docs/openapi.json")
        assert (
            response.headers.get("cross-origin-opener-policy")
            == "same-origin-allow-popups"
        )

    def test_api_path_keeps_strict_coop(self, test_client: TestClient[Any]) -> None:
        """API paths keep COOP same-origin."""
        response = test_client.get("/api/v1/healthz")
        assert response.headers.get("cross-origin-opener-policy") == "same-origin"


class TestDocsCspOriginsOverride:
    """``set_docs_csp_origins`` lets operators retarget Scalar UI origins."""

    @pytest.fixture(autouse=True)
    def _reset_docs_csp(self) -> Any:
        """Restore the default origins after each test in this class.

        Mutating the module-level CSP would leak across tests in the
        same xdist worker; this fixture is the boundary.
        """
        yield
        set_docs_csp_origins(_DOCS_CSP_DEFAULT_ORIGINS)

    def test_default_csp_lists_all_default_origins(self) -> None:
        for origin in _DOCS_CSP_DEFAULT_ORIGINS:
            assert origin in _DOCS_CSP

    def test_override_replaces_default_origins(self) -> None:
        from synthorg.api import middleware

        set_docs_csp_origins(["https://internal-cdn.example.com"])
        assert "https://internal-cdn.example.com" in middleware._DOCS_CSP
        assert "https://cdn.jsdelivr.net" not in middleware._DOCS_CSP

    def test_build_docs_csp_applies_origins_uniformly(self) -> None:
        # Parses the CSP into a directive map and asserts the origin
        # appears in every directive that is supposed to carry it.
        # Resilient to directive reordering or whitespace tweaks.
        origin = "https://only.example.com"
        result = build_docs_csp([origin])
        directives = {
            tokens[0]: tokens[1:]
            for raw in result.split(";")
            if (tokens := raw.strip().split()) and len(tokens) > 1
        }
        for name in ("script-src", "style-src", "img-src", "font-src", "connect-src"):
            assert origin in directives[name], (
                f"{name} missing {origin!r} in {directives[name]}"
            )

    def test_build_docs_csp_rejects_empty_origins(self) -> None:
        # Avoids producing a malformed CSP like
        # "script-src 'self' 'unsafe-inline' ;" with trailing whitespace
        # before the semicolon. The bridge config validator never lets
        # an empty tuple through, but the function fails fast as a
        # belt-and-braces check.
        with pytest.raises(ValueError, match="at least one trusted origin"):
            build_docs_csp([])


class TestApiBridgeConfigSecurity:
    """``ApiBridgeConfig`` rejects CSP origins that would degrade
    /docs CSP, and rejects non-HTTPS error-docs URLs."""

    @pytest.mark.parametrize(
        "bad_origin",
        [
            "javascript:alert(1)",
            "data:text/html,<img src=x>",
            "not a url",
            "ftp://example.com",
            "",
        ],
        ids=["javascript_scheme", "data_scheme", "not_a_url", "ftp_scheme", "empty"],
    )
    def test_csp_origins_validator_rejects_bad_schemes(self, bad_origin: str) -> None:
        from pydantic import ValidationError

        from synthorg.settings.bridge_configs import ApiBridgeConfig

        with pytest.raises(ValidationError):
            ApiBridgeConfig(
                csp_docs_external_origins=("https://cdn.example.com", bad_origin),
            )

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://docs.example.com/errors",
            "javascript:alert(1)",
            "ftp://docs.example.com",
        ],
        ids=["http_insecure", "javascript_scheme", "ftp_scheme"],
    )
    def test_error_docs_base_url_validator_rejects_non_https(
        self, bad_url: str
    ) -> None:
        from pydantic import ValidationError

        from synthorg.settings.bridge_configs import ApiBridgeConfig

        with pytest.raises(ValidationError):
            ApiBridgeConfig(error_docs_base_url=bad_url)

    def test_error_docs_base_url_accepts_https_override(self) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        cfg = ApiBridgeConfig(
            error_docs_base_url="https://docs.example.com/errors",
        )
        assert cfg.error_docs_base_url == "https://docs.example.com/errors"


# ── Cache-Control path selection ──────────────────────────────


class TestCacheControlPathSelection:
    """Verify path-aware Cache-Control via the before_send hook.

    API data endpoints get ``no-store`` (sensitive dynamic data).
    Documentation endpoints get ``public, max-age=300`` (static,
    unauthenticated, non-user-specific content).
    """

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/api/v1/healthz", _API_CACHE_CONTROL),
            ("/documents", _API_CACHE_CONTROL),
            ("/docsearch", _API_CACHE_CONTROL),
            ("/docs", _DOCS_CACHE_CONTROL),
            ("/docs/api", _DOCS_CACHE_CONTROL),
            ("/docs/openapi.json", _DOCS_CACHE_CONTROL),
        ],
        ids=[
            "api-no-store",
            "documents-no-store",
            "docsearch-no-store",
            "docs-exact-cached",
            "docs-subpath-cached",
            "docs-openapi-cached",
        ],
    )
    def test_cache_control_path_selection(
        self,
        test_client: TestClient[Any],
        path: str,
        expected: str,
    ) -> None:
        """Verify cache-control: no-store for API, brief caching for /docs."""
        response = test_client.get(path)
        assert response.headers.get("cache-control") == expected

    def test_docs_path_applies_cache_and_coop_relaxations(
        self, test_client: TestClient[Any]
    ) -> None:
        """Cache-Control and COOP relaxation both apply to /docs paths."""
        response = test_client.get("/docs/openapi.json")
        assert response.headers.get("cache-control") == _DOCS_CACHE_CONTROL
        assert (
            response.headers.get("cross-origin-opener-policy")
            == "same-origin-allow-popups"
        )

    def test_api_responses_carry_strict_cache_directives(
        self, test_client: TestClient[Any]
    ) -> None:
        """Every directive in the strengthened Cache-Control is present."""
        response = test_client.get("/api/v1/healthz")
        cache_header = response.headers.get("cache-control") or ""
        for directive in (
            "no-store",
            "no-cache",
            "must-revalidate",
            "max-age=0",
        ):
            assert directive in cache_header, (
                f"missing {directive!r} in {cache_header!r}"
            )

    def test_api_responses_carry_pragma_no_cache(
        self, test_client: TestClient[Any]
    ) -> None:
        """The HTTP/1.0 Pragma directive is set on every API response."""
        response = test_client.get("/api/v1/healthz")
        assert response.headers.get("pragma") == "no-cache"

    def test_docs_responses_omit_pragma_header(
        self, test_client: TestClient[Any]
    ) -> None:
        """Docs paths serve cacheable, non-user-specific content -- the
        ``Pragma: no-cache`` API hint MUST NOT bleed onto them.

        Strict ``is None`` check: an empty-string Pragma is still a
        regression because some intermediaries treat any Pragma header
        as a no-cache hint.
        """
        response = test_client.get("/docs/openapi.json")
        pragma = response.headers.get("pragma")
        assert pragma is None, f"docs response unexpectedly carries Pragma={pragma!r}"


# ── Request logging middleware ─────────────────────────────────


class TestRequestLoggingMiddleware:
    def test_request_completes_with_status(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/healthz")
        assert response.status_code == 200

    def test_not_found_returns_correct_status(
        self, test_client: TestClient[Any]
    ) -> None:
        response = test_client.get("/api/v1/agents/nonexistent")
        assert response.status_code == 404


class TestCorrelationIdBinding:
    """Verify request correlation ID lifecycle in middleware."""

    def test_correlation_id_bound_during_request(self) -> None:
        """Middleware binds a request_id into structlog context."""
        import structlog

        captured_id: str | None = None

        @get("/capture")
        async def handler() -> dict[str, str]:
            nonlocal captured_id
            ctx = structlog.contextvars.get_contextvars()
            captured_id = ctx.get("request_id")
            return {"ok": "true"}

        app = Litestar(
            route_handlers=[handler],
            middleware=[RequestLoggingMiddleware],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app) as client:
            resp = client.get("/capture")
            assert resp.status_code == 200

        assert captured_id is not None
        assert len(captured_id) > 0

    def test_correlation_id_cleared_after_request(self) -> None:
        """Context is cleaned up after request completes."""
        import structlog

        @get("/clear-test")
        async def handler() -> dict[str, str]:
            return {"ok": "true"}

        app = Litestar(
            route_handlers=[handler],
            middleware=[RequestLoggingMiddleware],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app) as client:
            client.get("/clear-test")

        # After request completes, request_id should be cleared
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    def test_correlation_id_cleared_on_error(self) -> None:
        """Context is cleaned up even when request raises."""
        import structlog

        @get("/error-clear")
        async def handler() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        app = Litestar(
            route_handlers=[handler],
            middleware=[RequestLoggingMiddleware],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app) as client:
            resp = client.get("/error-clear")
            assert resp.status_code == 500

        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx


class TestLogRequestCompletion:
    """Tests for the _log_request_completion helper."""

    def test_none_status_logs_warning_with_zero(self) -> None:
        """status_code=None logs at WARNING with status_code=0."""
        import structlog as _structlog

        with _structlog.testing.capture_logs() as logs:
            from synthorg.api.middleware import _log_request_completion

            _log_request_completion("GET", "/test", None, 42.0)

        assert len(logs) >= 1
        warn_logs = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert len(warn_logs) >= 1
        assert warn_logs[0]["status_code"] == 0
        assert warn_logs[0]["status_code_captured"] is False

    def test_known_status_logs_info(self) -> None:
        """Known status_code logs at INFO."""
        import structlog as _structlog

        with _structlog.testing.capture_logs() as logs:
            from synthorg.api.middleware import _log_request_completion

            _log_request_completion("POST", "/api", 201, 10.5)

        assert len(logs) >= 1
        info_logs = [entry for entry in logs if entry.get("log_level") == "info"]
        assert len(info_logs) >= 1
        assert info_logs[0]["status_code"] == 201
