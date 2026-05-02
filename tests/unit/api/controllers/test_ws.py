"""Tests for WebSocket handler message parsing and ticket auth."""

import json
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.auth.models import AuthenticatedUser, AuthMethod
from synthorg.api.controllers.ws import (
    _WS_CLOSE_AUTH_FAILED,
    _WS_CLOSE_FORBIDDEN,
)
from synthorg.api.controllers.ws_protocol import channel_allowed, handle_message
from synthorg.api.guards import _READ_ROLES, HumanRole

# Preserve the pre-split names so the (many) test bodies below read
# naturally -- matches the legacy identifiers used before the protocol
# helpers moved into ``ws_protocol``.
_channel_allowed = channel_allowed
_handle_message = handle_message

_TEST_USER = AuthenticatedUser(
    user_id="test-user",
    username="test",
    role=HumanRole.CEO,
    auth_method=AuthMethod.JWT,
)


@pytest.mark.unit
class TestWsHandleMessage:
    def test_subscribe(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data["action"] == "subscribed"
        assert "tasks" in data["channels"]
        assert "tasks" in subscribed

    def test_unsubscribe(self) -> None:
        subscribed: set[str] = {"tasks", "budget"}
        filters: dict[str, dict[str, str]] = {"tasks": {"agent_id": "a1"}}
        result = _handle_message(
            json.dumps({"action": "unsubscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data["action"] == "unsubscribed"
        assert "tasks" not in data["channels"]
        assert "budget" in data["channels"]
        assert "tasks" not in filters

    def test_invalid_json(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message("not json", subscribed, filters, _TEST_USER)
        data = json.loads(result)
        assert data["error"] == "Invalid JSON"

    def test_unknown_action(self) -> None:
        # The typed control-plane discriminator rejects unknown action
        # values at parse time, so the response is the boundary helper's
        # generic "invalid" envelope rather than the legacy
        # "Unknown action" string. The behaviour change is intentional:
        # malformed first-message content never reaches dispatch.
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "unknown"}),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data["error"] == "Invalid control message"

    def test_subscribe_ignores_invalid_channels(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks", "invalid"],
                }
            ),
            subscribed,
            filters,
            _TEST_USER,
        )
        assert "tasks" in subscribed
        assert "invalid" not in subscribed

    def test_subscribe_with_filters(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": {
                        "agent_id": "agent-1",
                        "project": "proj-1",
                    },
                }
            ),
            subscribed,
            filters,
            _TEST_USER,
        )
        assert "tasks" in subscribed
        assert filters["tasks"] == {
            "agent_id": "agent-1",
            "project": "proj-1",
        }

    def test_unsubscribe_clears_filters(self) -> None:
        subscribed: set[str] = {"tasks"}
        filters: dict[str, dict[str, str]] = {"tasks": {"agent_id": "agent-1"}}
        _handle_message(
            json.dumps({"action": "unsubscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
            _TEST_USER,
        )
        assert "tasks" not in subscribed
        assert "tasks" not in filters

    def test_subscribe_without_filters_keeps_existing(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
            _TEST_USER,
        )
        assert "tasks" in subscribed
        assert "tasks" not in filters

    def test_subscribe_too_many_filter_keys(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        many_filters = {f"key_{i}": f"val_{i}" for i in range(11)}
        result = _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": many_filters,
                }
            ),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data["error"] == "Filter bounds exceeded"
        assert "tasks" not in subscribed

    def test_subscribe_filter_value_too_long(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": {"key": "x" * 257},
                }
            ),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data["error"] == "Filter bounds exceeded"
        assert "tasks" not in subscribed

    def test_message_size_limit_boundary(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}

        # 4096 bytes should pass (valid JSON that fits)
        small_msg = json.dumps({"action": "subscribe", "channels": ["tasks"]})
        result = _handle_message(small_msg, subscribed, filters, _TEST_USER)
        data = json.loads(result)
        assert data["action"] == "subscribed"

        # Message whose encoded bytes exceed 4096 should fail
        big_msg = json.dumps({"action": "subscribe", "data": "x" * 4096})
        result = _handle_message(big_msg, subscribed, filters, _TEST_USER)
        data = json.loads(result)
        assert data["error"] == "Message too large"

    @pytest.mark.parametrize(
        "value",
        [[1, 2, 3], "hello", 42],
        ids=["array", "string", "number"],
    )
    def test_non_dict_json_returns_error(self, value: object) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(json.dumps(value), subscribed, filters, _TEST_USER)
        data = json.loads(result)
        assert data["error"] == "Expected JSON object"

    def test_ping_returns_pong(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "ping"}),
            subscribed,
            filters,
            _TEST_USER,
        )
        data = json.loads(result)
        assert data == {"action": "pong"}

    def test_ping_does_not_mutate_subscriptions(self) -> None:
        subscribed: set[str] = {"tasks"}
        filters: dict[str, dict[str, str]] = {"tasks": {"agent_id": "a1"}}
        _handle_message(
            json.dumps({"action": "ping"}),
            subscribed,
            filters,
            _TEST_USER,
        )
        assert subscribed == {"tasks"}
        assert filters == {"tasks": {"agent_id": "a1"}}


@pytest.mark.unit
class TestWsTicketAuth:
    """Tests for ticket-based WebSocket authentication logic.

    These tests validate the auth validation logic used by the WS
    handler without opening actual WebSocket connections (which
    require the channels plugin background task and hang in the
    sync test client).
    """

    def test_ws_ticket_endpoint_returns_ticket(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """POST /auth/ws-ticket returns a consumable ticket."""
        response = test_client.post("/api/v1/auth/ws-ticket")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "ticket" in data
        assert data["expires_in"] == 30

    def test_ws_ticket_carries_ws_ticket_auth_method(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The ticket user has auth_method=WS_TICKET."""
        response = test_client.post("/api/v1/auth/ws-ticket")
        ticket = response.json()["data"]["ticket"]

        app_state = test_client.app.state["app_state"]
        user = app_state.ticket_store.validate_and_consume(ticket)
        assert user is not None
        assert user.auth_method == AuthMethod.WS_TICKET

    def test_ws_ticket_single_use_via_store(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Ticket is consumed on first validate_and_consume."""
        response = test_client.post("/api/v1/auth/ws-ticket")
        ticket = response.json()["data"]["ticket"]

        app_state = test_client.app.state["app_state"]
        first = app_state.ticket_store.validate_and_consume(ticket)
        second = app_state.ticket_store.validate_and_consume(ticket)
        assert first is not None
        assert second is None

    def test_ws_ticket_user_has_correct_identity(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The ticket preserves the original user's identity."""
        response = test_client.post("/api/v1/auth/ws-ticket")
        ticket = response.json()["data"]["ticket"]

        app_state = test_client.app.state["app_state"]
        user = app_state.ticket_store.validate_and_consume(ticket)
        assert user is not None
        assert user.role == HumanRole.CEO
        assert user.username == "test-ceo"

    def test_ws_endpoint_excluded_from_auth_middleware(self) -> None:
        """The /ws path must be in the auto-derived auth exclude list."""
        from synthorg.api.config import ApiConfig
        from synthorg.api.middleware_factory import _build_middleware

        api_config = ApiConfig()
        middleware = _build_middleware(api_config)
        # Find auth middleware by class name (avoids brittle index).
        auth_cls = next(
            m
            for m in middleware
            if getattr(m, "__name__", "").endswith("AuthMiddleware")
        )

        from litestar import Litestar

        dummy_app = Litestar(route_handlers=[])
        instance = auth_cls(dummy_app)  # type: ignore[operator,call-arg]
        # Litestar compiles exclude paths into a single regex pattern.
        assert instance.exclude is not None  # type: ignore[union-attr]
        assert instance.exclude.match("/api/v1/ws"), (  # type: ignore[union-attr]
            f"/api/v1/ws not matched by exclude: {instance.exclude.pattern}"  # type: ignore[union-attr]
        )

    def test_read_roles_includes_all_human_roles_except_system(self) -> None:
        """_READ_ROLES includes all HumanRole values except SYSTEM.

        The SYSTEM role is scoped to backup/wipe endpoints only and
        is intentionally excluded from WebSocket access.
        """
        for role in HumanRole:
            if role == HumanRole.SYSTEM:
                assert role not in _READ_ROLES
            else:
                assert role in _READ_ROLES

    def test_ws_close_codes_in_application_range(self) -> None:
        """WS close codes should be in the RFC 6455 application range."""
        assert 4000 <= _WS_CLOSE_AUTH_FAILED <= 4999
        assert 4000 <= _WS_CLOSE_FORBIDDEN <= 4999

    def test_ws_rejects_invalid_query_ticket(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """WS connection with invalid query-param ticket is rejected pre-accept."""
        from litestar.exceptions import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            test_client.websocket_connect("/api/v1/ws?ticket=bogus-ticket"),
        ):
            pass
        assert exc_info.value.code == _WS_CLOSE_AUTH_FAILED, (
            f"Expected close code {_WS_CLOSE_AUTH_FAILED} for "
            f"invalid_ticket, got {exc_info.value.code}"
        )

    def test_ws_rejects_bad_first_message_ticket(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """WS connection with invalid first-message ticket is rejected post-accept."""
        from litestar.exceptions import WebSocketDisconnect

        def attempt() -> None:
            with test_client.websocket_connect("/api/v1/ws") as ws:
                ws.send_text(json.dumps({"action": "auth", "ticket": "bogus-ticket"}))
                ws.receive_text()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            attempt()
        assert exc_info.value.code == _WS_CLOSE_AUTH_FAILED

    def test_ws_rejects_missing_first_message_auth(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """WS connection sending non-auth first message is rejected."""
        from litestar.exceptions import WebSocketDisconnect

        def attempt() -> None:
            with test_client.websocket_connect("/api/v1/ws") as ws:
                ws.send_text(json.dumps({"action": "subscribe", "channels": ["tasks"]}))
                ws.receive_text()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            attempt()
        assert exc_info.value.code == _WS_CLOSE_AUTH_FAILED

    def test_ws_accepts_valid_ticket(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """WS connection with a valid ticket is accepted.

        The server first sends ``{"action": "auth_ok"}`` so the client
        can transition to ``connected=true`` only after auth completes.
        """
        from synthorg.api.auth.models import AuthenticatedUser, AuthMethod

        app_state = test_client.app.state["app_state"]
        user = AuthenticatedUser(
            user_id="test-ws-user",
            username="test-ceo",
            role=HumanRole.CEO,
            auth_method=AuthMethod.WS_TICKET,
            must_change_password=False,
        )
        ticket = app_state.ticket_store.create(user)

        with test_client.websocket_connect(
            f"/api/v1/ws?ticket={ticket}",
        ) as ws:
            ack = json.loads(ws.receive_text())
            assert ack == {"action": "auth_ok"}
            ws.send_text(
                json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            )
            resp = json.loads(ws.receive_text())
            assert resp["action"] == "subscribed"
            assert "tasks" in resp["channels"]

    def test_ws_first_message_auth_sends_auth_ok(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """First-message-auth path sends ``auth_ok`` after ticket validation.

        Closes the flash gap where ``connected=true`` was set client-side
        before the server validated the ticket.
        """
        from synthorg.api.auth.models import AuthenticatedUser, AuthMethod

        app_state = test_client.app.state["app_state"]
        user = AuthenticatedUser(
            user_id="test-ws-user-fm",
            username="test-ceo",
            role=HumanRole.CEO,
            auth_method=AuthMethod.WS_TICKET,
            must_change_password=False,
        )
        ticket = app_state.ticket_store.create(user)

        with test_client.websocket_connect("/api/v1/ws") as ws:
            ws.send_text(json.dumps({"action": "auth", "ticket": ticket}))
            ack = json.loads(ws.receive_text())
            assert ack == {"action": "auth_ok"}

    def test_ws_ping_pong_round_trip(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Client ping receives a server pong without affecting state."""
        from synthorg.api.auth.models import AuthenticatedUser, AuthMethod

        app_state = test_client.app.state["app_state"]
        user = AuthenticatedUser(
            user_id="test-ws-ping",
            username="test-ceo",
            role=HumanRole.CEO,
            auth_method=AuthMethod.WS_TICKET,
            must_change_password=False,
        )
        ticket = app_state.ticket_store.create(user)

        with test_client.websocket_connect(
            f"/api/v1/ws?ticket={ticket}",
        ) as ws:
            assert json.loads(ws.receive_text())["action"] == "auth_ok"
            ws.send_text(json.dumps({"action": "ping"}))
            resp = json.loads(ws.receive_text())
            assert resp == {"action": "pong"}

    def test_ws_auth_middleware_scopes_http_only(self) -> None:
        """Auth middleware must use HTTP-only scopes.

        WebSocket connections use ticket-based auth in the handler,
        not JWT/API key auth from the middleware.
        """
        from litestar import Litestar
        from litestar.enums import ScopeType

        from synthorg.api.config import ApiConfig
        from synthorg.api.middleware_factory import _build_middleware

        api_config = ApiConfig()
        middleware = _build_middleware(api_config)
        # Find auth middleware by class name (avoids brittle index).
        auth_cls = next(
            m
            for m in middleware
            if getattr(m, "__name__", "").endswith("AuthMiddleware")
        )

        dummy_app = Litestar(route_handlers=[])
        instance = auth_cls(dummy_app)  # type: ignore[operator,call-arg]
        assert instance.scopes == {ScopeType.HTTP}  # type: ignore[union-attr]

    def test_ws_rate_limit_excludes_ws_path(self) -> None:
        """Rate limit middleware must exclude the WS path.

        HTTP-style rate limiting makes no sense for persistent
        WebSocket connections and can cause spurious 403 rejections.
        """
        from litestar.middleware import DefineMiddleware
        from litestar.middleware.rate_limit import (
            RateLimitConfig as LitestarRateLimitConfig,
        )

        from synthorg.api.config import ApiConfig
        from synthorg.api.middleware_factory import _build_middleware

        api_config = ApiConfig()
        middleware = _build_middleware(api_config)

        # Find the rate limit middleware by type -- don't rely on index.
        rl_config = None
        for mw in middleware:
            if (
                isinstance(mw, DefineMiddleware)
                and "config" in mw.kwargs
                and isinstance(mw.kwargs["config"], LitestarRateLimitConfig)
            ):
                rl_config = mw.kwargs["config"]
                break
        assert rl_config is not None, (
            "Rate limit middleware not found in middleware stack"
        )

        ws_path = f"^{api_config.api_prefix}/ws$"
        assert ws_path in (rl_config.exclude or []), (
            f"WS path '{ws_path}' not excluded from rate limit: {rl_config.exclude}"
        )

    def test_ws_handler_signature_no_channels_plugin_param(self) -> None:
        """ws_handler must NOT declare ChannelsPlugin as a parameter.

        Litestar's DI misidentifies plugin params as query params
        for WebSocket handlers, causing a Litestar-internal 4500
        close before the handler runs. The handler must resolve the
        plugin from ``socket.app.plugins`` instead.
        """
        import inspect

        from synthorg.api.controllers.ws import ws_handler

        sig = inspect.signature(ws_handler.fn)
        param_names = list(sig.parameters.keys())
        assert "channels_plugin" not in param_names, (
            "channels_plugin must not be a handler parameter -- "
            "resolve from socket.app.plugins instead (see #549)"
        )

    def test_ws_handler_opt_exclude_from_auth(self) -> None:
        """ws_handler must set opt={"exclude_from_auth": True}.

        Defense-in-depth marker signaling Litestar's auth middleware
        to skip this handler.  Works alongside ScopeType.HTTP scoping
        and regex path exclusion.
        """
        from synthorg.api.controllers.ws import ws_handler

        assert ws_handler.opt.get("exclude_from_auth") is True, (
            "ws_handler must have opt={'exclude_from_auth': True} "
            "as a defense-in-depth auth exclusion marker"
        )

    def test_ws_guard_passes_without_user_in_scope(self) -> None:
        """require_password_changed guard must pass for WS connections.

        The auth middleware is HTTP-only, so WebSocket upgrade requests
        arrive at the guard with no user in scope.  The guard must
        return without raising PermissionDeniedException.  The actual
        WS auth happens via ticket validation inside the handler.
        """
        from unittest.mock import MagicMock

        from synthorg.api.auth.controller_helpers import require_password_changed

        connection = MagicMock()
        connection.url.path = "/api/v1/ws"
        connection.scope = {"type": "websocket"}

        # Guard must not raise PermissionDeniedException when user is
        # absent -- this is the expected state for WS connections.
        require_password_changed(connection, MagicMock())
        # Reaching here without PermissionDeniedException confirms
        # the guard passes through for WS scope with no user.


@pytest.mark.unit
class TestOutboundPipeline:
    """Tests for outbound event size cap + per-client backpressure queue."""

    async def test_oversized_event_dropped_without_close(self) -> None:
        """Events whose JSON exceeds 32 KiB are dropped, socket stays open."""
        import asyncio
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.ws import (
            _MAX_OUTBOUND_EVENT_BYTES,
            _on_event,
        )

        socket = AsyncMock()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        big_payload = "x" * (_MAX_OUTBOUND_EVENT_BYTES + 1)
        event_data = json.dumps(
            {
                "channel": "tasks",
                "event_type": "task.created",
                "timestamp": "2026-04-21T00:00:00+00:00",
                "payload": {"data": big_payload},
            },
        ).encode()

        await _on_event(
            event_data,
            {"tasks"},
            {},
            queue,
            _TEST_USER,
        )

        assert queue.empty()
        socket.send_text.assert_not_called()
        socket.close.assert_not_called()

    async def test_within_size_event_enqueued(self) -> None:
        """Events under the size cap are enqueued for the consumer."""
        import asyncio

        from synthorg.api.controllers.ws import _on_event

        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        event_data = json.dumps(
            {
                "channel": "tasks",
                "event_type": "task.created",
                "timestamp": "2026-04-21T00:00:00+00:00",
                "payload": {"task_id": "t-1"},
            },
        ).encode()

        await _on_event(
            event_data,
            {"tasks"},
            {},
            queue,
            _TEST_USER,
        )

        assert queue.qsize() == 1
        assert queue.get_nowait() == event_data

    async def test_backpressure_drops_when_queue_full(self) -> None:
        """When the per-client queue is full, additional events are dropped.

        The queue does not grow beyond its bound, the socket is not
        closed, and the established events remain readable.
        """
        import asyncio

        from synthorg.api.controllers.ws import _on_event

        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)

        def make_event(idx: int) -> bytes:
            return json.dumps(
                {
                    "channel": "tasks",
                    "event_type": "task.created",
                    "timestamp": "2026-04-21T00:00:00+00:00",
                    "payload": {"task_id": f"t-{idx}"},
                },
            ).encode()

        for idx in range(5):
            await _on_event(
                make_event(idx),
                {"tasks"},
                {},
                queue,
                _TEST_USER,
            )

        assert queue.qsize() == 2

    async def test_outbound_consumer_drains_queue(self) -> None:
        """The consumer task forwards every queued event to the socket."""
        import asyncio
        import contextlib
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.ws import _outbound_consumer

        socket = AsyncMock()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
        await queue.put(b'{"channel":"tasks","payload":{"a":1}}')
        await queue.put(b'{"channel":"tasks","payload":{"a":2}}')

        task = asyncio.create_task(_outbound_consumer(socket, queue))
        try:
            await asyncio.wait_for(queue.join(), timeout=1.0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert socket.send_text.await_count == 2

    async def test_outbound_consumer_closes_socket_on_send_error(self) -> None:
        """A non-disconnect send failure triggers a 1011 close and exits.

        Ensures arbitrary transport errors on ``socket.send_text`` do
        not surface as silently dropped events: the consumer must log,
        close the socket, and stop draining (which in turn unwinds the
        subscription via the surrounding ``run_in_background``).
        """
        import asyncio
        import contextlib
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.ws import _outbound_consumer

        socket = AsyncMock()
        # Simulate a transient transport error (not a WebSocketDisconnect
        # -- the consumer treats those as a normal shutdown).
        socket.send_text.side_effect = RuntimeError("socket fell over")
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
        await queue.put(b'{"channel":"tasks","payload":{"a":1}}')

        task = asyncio.create_task(_outbound_consumer(socket, queue))
        try:
            await asyncio.wait_for(task, timeout=1.0)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        socket.send_text.assert_awaited_once()
        socket.close.assert_awaited_once_with(
            code=1011,
            reason="Internal error",
        )


@pytest.mark.unit
class TestChannelAllowed:
    """Tests for _channel_allowed server-side access control."""

    def test_user_channel_allowed_for_owner(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="owner",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )
        assert _channel_allowed("user:u1", user) is True

    def test_user_channel_denied_for_non_owner(self) -> None:
        user = AuthenticatedUser(
            user_id="u2",
            username="other",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )
        assert _channel_allowed("user:u1", user) is False

    def test_budget_channel_allowed_for_ceo(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="ceo",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )
        assert _channel_allowed("budget", user) is True

    def test_budget_channel_denied_for_observer(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="observer",
            role=HumanRole.OBSERVER,
            auth_method=AuthMethod.JWT,
        )
        assert _channel_allowed("budget", user) is False

    def test_normal_channel_allowed_for_all(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="observer",
            role=HumanRole.OBSERVER,
            auth_method=AuthMethod.JWT,
        )
        assert _channel_allowed("tasks", user) is True


@pytest.mark.unit
class TestSubscribeAccessControl:
    """Tests for subscribe channel filtering based on user identity."""

    def test_own_user_channel_accepted(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="owner",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "subscribe", "channels": ["user:u1"]}),
            subscribed,
            filters,
            user,
        )
        data = json.loads(result)
        assert "user:u1" in data["channels"]
        assert "user:u1" in subscribed

    def test_other_user_channel_silently_dropped(self) -> None:
        user = AuthenticatedUser(
            user_id="u1",
            username="owner",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "subscribe", "channels": ["user:u2"]}),
            subscribed,
            filters,
            user,
        )
        data = json.loads(result)
        assert "user:u2" not in data["channels"]
        assert "user:u2" not in subscribed
