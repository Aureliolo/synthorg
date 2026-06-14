"""Tests for the A2A JSON-RPC 2.0 gateway controller helpers."""

import pytest
from litestar import Request
from litestar.testing import RequestFactory

from synthorg.a2a.gateway import (
    _error_response,
    _extract_peer_name,
    _success_response,
)
from synthorg.a2a.models import (
    A2A_PEER_NOT_ALLOWED,
    JSONRPC_METHOD_NOT_FOUND,
)
from synthorg.a2a.rpc_params import A2AMessageSendParams
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.integrations.connections.catalog import ConnectionCatalog
from tests._shared import as_uuid, make_app_state, mock_of, sid


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
        error = resp["error"]
        assert isinstance(error, dict)
        assert error["code"] == -32601
        assert error["message"] == "Method not found"
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
        error = resp["error"]
        assert isinstance(error, dict)
        data = error["data"]
        assert isinstance(data, dict)
        assert data["peer"] == "unknown"

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
        result = resp["result"]
        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert resp["error"] is None


class TestExtractPeerName:
    """Peer name extraction from request headers."""

    @pytest.mark.unit
    def test_from_header(self) -> None:
        """Extracts peer name from X-A2A-Peer-Name header."""

        request = RequestFactory().get(
            path="/", headers={"x-a2a-peer-name": "peer-alpha"}
        )
        result = _extract_peer_name(request)
        assert result == "peer-alpha"

    @pytest.mark.unit
    def test_strips_whitespace(self) -> None:
        """Strips whitespace from peer name."""

        request = RequestFactory().get(
            path="/", headers={"x-a2a-peer-name": "  peer-beta  "}
        )
        result = _extract_peer_name(request)
        assert result == "peer-beta"

    @pytest.mark.unit
    def test_missing_header(self) -> None:
        """Returns None when header is absent."""

        request = RequestFactory().get(path="/")
        result = _extract_peer_name(request)
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
        """Ownership check accepts any authenticated peer."""
        from synthorg.a2a.gateway import _validate_task_ownership

        # Should not raise; the task argument is never inspected.
        _validate_task_ownership(_make_task("task-0", TaskStatus.CREATED), "any-peer")


class TestRequireTaskEngine:
    """Task engine availability check."""

    @pytest.mark.unit
    def test_returns_engine_when_available(self) -> None:
        """Returns the task engine when wired."""
        from synthorg.a2a.gateway import _require_task_engine
        from synthorg.engine.task_engine import TaskEngine

        task_engine = mock_of[TaskEngine]()
        app_state = make_app_state(task_engine=task_engine)
        result = _require_task_engine(app_state)
        assert result is task_engine

    @pytest.mark.unit
    def test_raises_method_error_when_unavailable(self) -> None:
        """Raises _A2AMethodError when engine not wired."""
        from synthorg.a2a.gateway import _A2AMethodError, _require_task_engine

        app_state = make_app_state()
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

        app_state = make_app_state()
        request = MagicMock(spec=Request)

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_empty_credentials_fails_closed(self) -> None:
        """A configured catalog with no credentials for an allowlisted peer
        FAILS CLOSED: an operator who adds a peer but forgets its
        credentials must not silently grant access."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(return_value={})
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_api_key_match_returns_true(self) -> None:
        """Matching API key passes."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "secret-123"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
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

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "correct"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
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

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": "stored"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
        request.headers = {}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_blank_stored_api_key_fails_closed(self) -> None:
        """A catalog record whose api_key is blank must not grant access."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "api_key", "api_key": ""},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
        # Present a key: a blank stored value must still deny, not match.
        request.headers = {"x-api-key": "anything"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_blank_stored_access_token_fails_closed(self) -> None:
        """A catalog record whose access_token is blank must not grant access."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "bearer", "access_token": ""},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
        request.headers = {"authorization": "Bearer anything"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_bearer_token_missing_in_request_fails_closed(self) -> None:
        """A stored bearer token with no token presented is denied."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "bearer", "access_token": "stored"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
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

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={
                "auth_scheme": "bearer",
                "access_token": "tok-abc",
            },
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
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

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={
                "auth_scheme": "bearer",
                "access_token": "correct",
            },
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
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

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "mtls"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
        request.headers = {}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is True

    @pytest.mark.unit
    async def test_unsupported_scheme_fails_closed(self) -> None:
        """An unknown auth_scheme (typo / misconfiguration) is denied.

        It must not fall through to the trailing ``return True`` and grant
        access without any credential check.
        """
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            return_value={"auth_scheme": "totally-bogus", "api_key": "x"},
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)
        request.headers = {"x-api-key": "x"}

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False

    @pytest.mark.unit
    async def test_catalog_error_denies(self) -> None:
        """Catalog errors result in denial."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.a2a.gateway import _verify_peer_credentials

        catalog = AsyncMock(spec=ConnectionCatalog)
        catalog.get_credentials = AsyncMock(
            side_effect=RuntimeError("db down"),
        )
        app_state = make_app_state(connection_catalog=catalog)
        request = MagicMock(spec=Request)

        result = await _verify_peer_credentials(
            app_state,
            request,
            "peer-a",
        )
        assert result is False


def _make_task(task_id: str, status: TaskStatus) -> Task:
    """Build a real ``Task`` carrying the given ``id`` and ``status``.

    The gateway handlers read only ``id`` (response payload) and
    ``status`` (mapped through :func:`to_a2a`); the remaining fields are
    fixed defaults. A real model is used because ``Task`` fields are
    pydantic instance attributes that ``create_autospec`` cannot spec.
    """
    return Task(
        id=as_uuid(task_id),
        title="A2A task",
        description="A2A inbound task",
        type=TaskType.ADMIN,
        project="a2a-inbound",
        created_by="a2a-gateway",
        status=status,
        assigned_to=None if status is TaskStatus.CREATED else "a2a-agent",
    )


def _send_params(parts: int = 1) -> A2AMessageSendParams:
    """Build a ``message/send`` params model with ``parts`` text parts."""
    from synthorg.a2a.models import A2AMessage, A2AMessageRole, A2ATextPart

    return A2AMessageSendParams(
        message=A2AMessage(
            role=A2AMessageRole.USER,
            parts=tuple(A2ATextPart(text=f"part-{i}") for i in range(parts)),
        ),
    )


class TestHandleMessageSend:
    """``message/send`` handler creates a task via the engine convenience API."""

    @pytest.mark.unit
    async def test_creates_task_and_returns_state(self) -> None:
        """Creates the task through ``create_task`` and maps its status."""
        from synthorg.a2a.gateway import _handle_message_send
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        task = _make_task("task-1", TaskStatus.IN_PROGRESS)
        engine = mock_of[TaskEngine]()
        engine.create_task.return_value = task
        app_state = make_app_state(task_engine=engine)

        result = await _handle_message_send(app_state, _send_params(), "peer-a")

        assert result == {"id": sid("task-1"), "state": "working"}
        engine.create_task.assert_awaited_once()
        call = engine.create_task.call_args
        assert call.kwargs["requested_by"] == "a2a-gateway:peer-a"
        task_data = call.args[0]
        assert task_data.project == "a2a-inbound"
        assert task_data.created_by == "a2a-gateway"

    @pytest.mark.unit
    async def test_rejects_message_exceeding_part_cap(self) -> None:
        """A message with more parts than the cap raises invalid-params."""
        from unittest.mock import AsyncMock, patch

        from synthorg.a2a.gateway import (
            _A2AMethodError,
            _handle_message_send,
            _resolve_max_message_parts,
        )
        from synthorg.a2a.models import JSONRPC_INVALID_PARAMS
        from synthorg.engine.task_engine import TaskEngine

        engine = mock_of[TaskEngine]()
        app_state = make_app_state(task_engine=engine)
        capped = AsyncMock(spec=_resolve_max_message_parts, return_value=1)

        with (
            patch("synthorg.a2a.gateway._resolve_max_message_parts", new=capped),
            pytest.raises(_A2AMethodError) as exc_info,
        ):
            await _handle_message_send(app_state, _send_params(parts=2), "peer-a")
        assert exc_info.value.code == JSONRPC_INVALID_PARAMS
        engine.create_task.assert_not_awaited()

    @pytest.mark.unit
    async def test_message_at_part_cap_is_accepted(self) -> None:
        """A message with exactly the cap is accepted (boundary)."""
        from unittest.mock import AsyncMock, patch

        from synthorg.a2a.gateway import (
            _handle_message_send,
            _resolve_max_message_parts,
        )
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        engine = mock_of[TaskEngine]()
        engine.create_task.return_value = _make_task("task-1", TaskStatus.IN_PROGRESS)
        app_state = make_app_state(task_engine=engine)
        at_cap = AsyncMock(spec=_resolve_max_message_parts, return_value=2)

        with patch("synthorg.a2a.gateway._resolve_max_message_parts", new=at_cap):
            result = await _handle_message_send(app_state, _send_params(parts=2), "p")
        assert result == {"id": sid("task-1"), "state": "working"}
        engine.create_task.assert_awaited_once()


class TestHandleTasksGet:
    """``tasks/get`` handler retrieves task state via ``get_task``."""

    @pytest.mark.unit
    async def test_returns_state_for_existing_task(self) -> None:
        """Maps an existing task's status to the A2A state."""
        from synthorg.a2a.gateway import _handle_tasks_get
        from synthorg.a2a.rpc_params import A2ATaskGetParams
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        task = _make_task("task-9", TaskStatus.COMPLETED)
        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = task
        app_state = make_app_state(task_engine=engine)

        result = await _handle_tasks_get(
            app_state,
            A2ATaskGetParams(id="task-9"),
            "peer-a",
        )

        assert result == {"id": sid("task-9"), "state": "completed"}
        engine.get_task.assert_awaited_once_with("task-9")

    @pytest.mark.unit
    async def test_missing_task_raises_404(self) -> None:
        """A missing task surfaces as a 404 method error."""
        from synthorg.a2a.gateway import _A2AMethodError, _handle_tasks_get
        from synthorg.a2a.models import A2A_TASK_NOT_FOUND
        from synthorg.a2a.rpc_params import A2ATaskGetParams
        from synthorg.engine.task_engine import TaskEngine

        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = None
        app_state = make_app_state(task_engine=engine)

        with pytest.raises(_A2AMethodError) as exc_info:
            await _handle_tasks_get(
                app_state,
                A2ATaskGetParams(id="missing"),
                "peer-a",
            )
        assert exc_info.value.http_status == 404
        assert exc_info.value.code == A2A_TASK_NOT_FOUND


class TestHandleTasksCancel:
    """``tasks/cancel`` handler pre-checks, then cancels via ``cancel_task``."""

    @pytest.mark.unit
    async def test_cancels_non_terminal_task(self) -> None:
        """Cancels a cancellable task and returns the updated state."""
        from synthorg.a2a.gateway import _handle_tasks_cancel
        from synthorg.a2a.rpc_params import A2ATaskCancelParams
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        active = _make_task("task-2", TaskStatus.IN_PROGRESS)
        cancelled = _make_task("task-2", TaskStatus.CANCELLED)
        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = active
        engine.cancel_task.return_value = (cancelled, TaskStatus.IN_PROGRESS)
        app_state = make_app_state(task_engine=engine)

        result = await _handle_tasks_cancel(
            app_state,
            A2ATaskCancelParams(id="task-2"),
            "peer-a",
        )

        assert result == {"id": sid("task-2"), "state": "canceled"}
        cancel_kwargs = engine.cancel_task.call_args.kwargs
        assert cancel_kwargs["requested_by"] == "a2a-gateway:peer-a"
        assert cancel_kwargs["reason"] == "A2A tasks/cancel request"

    @pytest.mark.unit
    async def test_missing_task_raises_404(self) -> None:
        """A cancel for an unknown task surfaces as 404 before cancelling."""
        from synthorg.a2a.gateway import _A2AMethodError, _handle_tasks_cancel
        from synthorg.a2a.models import A2A_TASK_NOT_FOUND
        from synthorg.a2a.rpc_params import A2ATaskCancelParams
        from synthorg.engine.task_engine import TaskEngine

        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = None
        app_state = make_app_state(task_engine=engine)

        with pytest.raises(_A2AMethodError) as exc_info:
            await _handle_tasks_cancel(
                app_state,
                A2ATaskCancelParams(id="missing"),
                "peer-a",
            )
        assert exc_info.value.http_status == 404
        assert exc_info.value.code == A2A_TASK_NOT_FOUND
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.unit
    async def test_terminal_task_is_not_cancelable(self) -> None:
        """A task already in a terminal state is rejected before cancelling."""
        from synthorg.a2a.gateway import _A2AMethodError, _handle_tasks_cancel
        from synthorg.a2a.models import A2A_TASK_NOT_CANCELABLE
        from synthorg.a2a.rpc_params import A2ATaskCancelParams
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        done = _make_task("task-3", TaskStatus.COMPLETED)
        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = done
        app_state = make_app_state(task_engine=engine)

        with pytest.raises(_A2AMethodError) as exc_info:
            await _handle_tasks_cancel(
                app_state,
                A2ATaskCancelParams(id="task-3"),
                "peer-a",
            )
        assert exc_info.value.code == A2A_TASK_NOT_CANCELABLE
        engine.cancel_task.assert_not_awaited()


class TestDispatchEngineErrors:
    """``_dispatch_method`` maps engine errors to the right JSON-RPC status."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("error_name", "expected_status"),
        [
            ("TaskEngineNotRunningError", 503),
            ("TaskEngineQueueFullError", 503),
            ("TaskNotFoundError", 404),
            ("TaskMutationError", 400),
        ],
    )
    async def test_engine_error_maps_to_status(
        self,
        error_name: str,
        expected_status: int,
    ) -> None:
        """Engine errors raised mid-cancel map to their wire status, not 500."""
        from synthorg.a2a import gateway as gw
        from synthorg.a2a.gateway import _dispatch_method
        from synthorg.a2a.models import JsonRpcRequest
        from synthorg.core.task_enums import TaskStatus
        from synthorg.engine.task_engine import TaskEngine

        error_cls = getattr(gw, error_name)
        active = _make_task("task-x", TaskStatus.IN_PROGRESS)
        engine = mock_of[TaskEngine]()
        engine.get_task.return_value = active
        engine.cancel_task.side_effect = error_cls("engine failure")
        app_state = make_app_state(task_engine=engine)

        request = JsonRpcRequest(
            method="tasks/cancel", id="r1", params={"id": "task-x"}
        )
        response = await _dispatch_method(app_state, request, "peer-a")

        assert response.status_code == expected_status
