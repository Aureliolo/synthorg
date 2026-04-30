"""Tests for the A2A JSON-RPC 2.0 gateway controller helpers."""

from typing import ClassVar

import pytest

from synthorg.a2a.gateway import (
    _error_response,
    _extract_peer_name,
    _success_response,
)
from synthorg.a2a.models import (
    A2A_PEER_NOT_ALLOWED,
    JSONRPC_METHOD_NOT_FOUND,
)


class TestErrorResponse:
    """JSON-RPC error response builder."""

    @pytest.mark.unit
    def test_structure(self) -> None:
        """Error response has correct JSON-RPC structure."""
        resp = _error_response(
            "req-1",
            JSONRPC_METHOD_NOT_FOUND,
            "Method not found",
        )
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "req-1"
        assert resp["error"]["code"] == -32601
        assert resp["error"]["message"] == "Method not found"
        assert resp["result"] is None

    @pytest.mark.unit
    def test_with_data(self) -> None:
        """Error response can carry additional data."""
        resp = _error_response(
            "req-1",
            A2A_PEER_NOT_ALLOWED,
            "Not allowed",
            data={"peer": "unknown"},
        )
        assert resp["error"]["data"]["peer"] == "unknown"

    @pytest.mark.unit
    def test_null_id(self) -> None:
        """Error response with null id (parse errors)."""
        resp = _error_response(None, -32700, "Parse error")
        assert resp["id"] is None


class TestSuccessResponse:
    """JSON-RPC success response builder."""

    @pytest.mark.unit
    def test_structure(self) -> None:
        """Success response has correct JSON-RPC structure."""
        resp = _success_response("req-1", {"status": "ok"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "req-1"
        assert resp["result"]["status"] == "ok"
        assert resp["error"] is None


class TestExtractPeerName:
    """Peer name extraction from request headers."""

    @pytest.mark.unit
    def test_from_header(self) -> None:
        """Extracts peer name from X-A2A-Peer-Name header."""

        class FakeRequest:
            headers: ClassVar[dict[str, str]] = {
                "x-a2a-peer-name": "peer-alpha",
            }

        result = _extract_peer_name(FakeRequest())  # type: ignore[arg-type]
        assert result == "peer-alpha"

    @pytest.mark.unit
    def test_strips_whitespace(self) -> None:
        """Strips whitespace from peer name."""

        class FakeRequest:
            headers: ClassVar[dict[str, str]] = {
                "x-a2a-peer-name": "  peer-beta  ",
            }

        result = _extract_peer_name(FakeRequest())  # type: ignore[arg-type]
        assert result == "peer-beta"

    @pytest.mark.unit
    def test_missing_header(self) -> None:
        """Returns None when header is absent."""

        class FakeRequest:
            headers: ClassVar[dict[str, str]] = {}

        result = _extract_peer_name(FakeRequest())  # type: ignore[arg-type]
        assert result is None


class TestSupportedMethods:
    """Verify supported method set."""

    @pytest.mark.unit
    def test_all_methods(self) -> None:
        """All expected methods are in the supported set."""
        from synthorg.a2a.gateway import _SUPPORTED_METHODS

        expected = {
            "message/send",
            "tasks/get",
            "tasks/cancel",
        }
        assert expected == _SUPPORTED_METHODS


class TestMethodHandlers:
    """Method handler registration."""

    @pytest.mark.unit
    def test_typed_union_covers_every_supported_method(self) -> None:
        """The ``A2ARpcParams`` discriminated union covers every supported method.

        Replaces the old ``_METHOD_HANDLERS`` dict assertion: dispatch
        is now structural (``match params:``) and the invariant moves to
        the discriminator literals on each variant model.
        """
        from typing import get_args

        from synthorg.a2a.gateway import _SUPPORTED_METHODS
        from synthorg.a2a.rpc_params import A2ARpcParams

        # ``Annotated[Union[...], Discriminator(...)]`` -> first arg
        # is the union itself; ``get_args`` on the union yields the
        # variant models.  Every variant has a ``method`` field
        # whose default is its ``Literal`` value.
        union_alias, _discriminator = get_args(A2ARpcParams)
        variants = get_args(union_alias)
        method_literals = {
            variant.model_fields["method"].default for variant in variants
        }
        assert method_literals == _SUPPORTED_METHODS

    @pytest.mark.unit
    def test_handler_functions_are_async_callable(self) -> None:
        """All three method handlers are async callables."""
        import inspect

        from synthorg.a2a.gateway import (
            _handle_message_send,
            _handle_tasks_cancel,
            _handle_tasks_get,
        )

        for handler in (
            _handle_message_send,
            _handle_tasks_get,
            _handle_tasks_cancel,
        ):
            assert callable(handler)
            assert inspect.iscoroutinefunction(handler)


class TestParseJsonrpc:
    """JSON-RPC request parsing."""

    @pytest.mark.unit
    def test_valid_request(self) -> None:
        """Valid JSON-RPC request is parsed successfully."""
        from synthorg.a2a.gateway import _parse_jsonrpc

        body = b'{"jsonrpc":"2.0","id":"1","method":"message/send","params":{}}'
        result = _parse_jsonrpc(body)
        assert result is not None
        assert result.method == "message/send"

    @pytest.mark.unit
    def test_invalid_json(self) -> None:
        """Invalid JSON returns None."""
        from synthorg.a2a.gateway import _parse_jsonrpc

        result = _parse_jsonrpc(b"not json {{{")
        assert result is None

    @pytest.mark.unit
    def test_missing_method(self) -> None:
        """Missing method field returns None."""
        from synthorg.a2a.gateway import _parse_jsonrpc

        result = _parse_jsonrpc(b'{"jsonrpc":"2.0","id":"1","params":{}}')
        assert result is None

    @pytest.mark.unit
    def test_empty_body(self) -> None:
        """Empty body returns None."""
        from synthorg.a2a.gateway import _parse_jsonrpc

        result = _parse_jsonrpc(b"")
        assert result is None


class TestA2AMethodError:
    """Internal method error."""

    @pytest.mark.unit
    def test_default_http_status(self) -> None:
        """Default HTTP status is 400."""
        from synthorg.a2a.gateway import _A2AMethodError

        err = _A2AMethodError(-32602, "Invalid params")
        assert err.http_status == 400
        assert err.code == -32602
        assert err.message == "Invalid params"

    @pytest.mark.unit
    def test_custom_http_status(self) -> None:
        """Custom HTTP status is respected."""
        from synthorg.a2a.gateway import _A2AMethodError

        err = _A2AMethodError(-32001, "Not found", http_status=404)
        assert err.http_status == 404


class TestValidateTaskOwnership:
    """Task ownership validation stub."""

    @pytest.mark.unit
    def test_ownership_check_is_noop(self) -> None:
        """Phase 1: ownership check accepts any authenticated peer."""
        from synthorg.a2a.gateway import _validate_task_ownership

        # Should not raise
        _validate_task_ownership(object(), "any-peer")


class TestRequireTaskEngine:
    """Task engine availability check."""

    @pytest.mark.unit
    def test_returns_engine_when_available(self) -> None:
        """Returns the task engine when wired."""
        from unittest.mock import MagicMock

        from synthorg.a2a.gateway import _require_task_engine
        from synthorg.api.state import AppState

        app_state = MagicMock(spec=AppState)
        app_state.task_engine = MagicMock()
        result = _require_task_engine(app_state)
        assert result is app_state.task_engine

    @pytest.mark.unit
    def test_raises_method_error_when_unavailable(self) -> None:
        """Raises _A2AMethodError when engine not wired."""
        from unittest.mock import MagicMock, PropertyMock

        from synthorg.a2a.gateway import _A2AMethodError, _require_task_engine
        from synthorg.api.state import AppState
        from synthorg.core.domain_errors import ServiceUnavailableError

        app_state = MagicMock(spec=AppState)
        type(app_state).task_engine = PropertyMock(
            side_effect=ServiceUnavailableError("task_engine"),
        )
        with pytest.raises(_A2AMethodError) as exc_info:
            _require_task_engine(app_state)
        assert exc_info.value.http_status == 503


class TestVerifyPeerCredentials:
    """Peer credential verification against connection catalog."""

    @pytest.mark.unit
    async def test_no_catalog_returns_true(self) -> None:
        """No catalog: graceful pass-through."""
        from unittest.mock import MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        app_state = MagicMock()
        app_state._connection_catalog = None
        request = MagicMock()

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_empty_credentials_returns_true(self) -> None:
        """Peer has no stored credentials: pass-through."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(return_value={})
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_api_key_match_returns_true(self) -> None:
        """Matching API key passes."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "secret-123"},
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {"x-api-key": "secret-123"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_api_key_mismatch_returns_false(self) -> None:
        """Mismatched API key is rejected."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "correct"},
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {"x-api-key": "wrong"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_missing_api_key_header_returns_false(self) -> None:
        """Missing API key header when stored key exists."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "stored"},
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_bearer_token_match(self) -> None:
        """Matching bearer token passes."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={
                "auth_scheme": "bearer",
                "access_token": "tok-abc",
            },
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {"authorization": "Bearer tok-abc"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_bearer_token_mismatch(self) -> None:
        """Mismatched bearer token is rejected."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={
                "auth_scheme": "bearer",
                "access_token": "correct",
            },
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {"authorization": "Bearer wrong"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_mtls_scheme_passes(self) -> None:
        """mTLS scheme has no header-level check."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "mtls"},
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()
        request.headers = {}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_catalog_error_denies(self) -> None:
        """Catalog errors result in denial."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock()
        catalog.get_credentials = AsyncMock(
            side_effect=RuntimeError("db down"),
        )
        app_state = MagicMock()
        app_state._connection_catalog = catalog
        request = MagicMock()

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False
