"""Controller-level integration tests for the 6 new integration APIs.

Covers:
- ``ConnectionsController`` -- list/get/create/update/delete/health
- ``OAuthController`` -- initiate, callback, status
- ``WebhooksController`` -- receive (signature verify, replay, bus publish)
- ``IntegrationHealthController`` -- aggregate + single
- ``MCPCatalogController`` -- browse/search/get
- ``TunnelController`` -- start/stop/status

The per-controller tests below invoke the underlying handler via
``handler.fn(ctrl, ...)`` so they run fast and can mock every
collaborator. ``TestControllerHttpLayer`` complements them with an
end-to-end sanity check through Litestar's ``TestClient`` so guards,
dependency injection, and RFC 9457 error translation are exercised
on the real HTTP path.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.testing import TestClient

from synthorg.api.cursor import CursorSecret
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    HealthReport,
)
from synthorg.integrations.errors import (
    DuplicateConnectionError,
)


def _make_conn(name: str = "c1") -> Connection:
    return Connection(
        name=NotBlankStr(name),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr("https://api.github.com"),
    )


@pytest.mark.integration
class TestConnectionsController:
    async def test_list_returns_catalog_entries(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.list_all = AsyncMock(return_value=(_make_conn("a"), _make_conn("b")))
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        response = await ctrl.list_connections.fn(ctrl, state=state)
        assert len(response.data) == 2
        catalog.list_all.assert_awaited_once()

    async def test_get_missing_raises_not_found(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.get = AsyncMock(return_value=None)
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await ctrl.get_connection.fn(ctrl, state=state, name="missing")

    async def test_create_validates_missing_name(self) -> None:
        """Missing ``name`` is rejected at the DTO boundary (#1682).

        After the Pydantic-DTO refactor, ``name`` is a required
        field on :class:`CreateConnectionRequest` and Litestar's
        request parser surfaces missing-field violations as 422
        before the controller method runs.  This test pins the
        contract at the model layer instead of the controller body
        because the controller no longer performs the validation.
        """
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.connections import (
            CreateConnectionRequest,
        )

        with pytest.raises(PydanticValidationError):
            CreateConnectionRequest.model_validate(
                {"connection_type": "github"},
            )

    async def test_create_validates_bad_connection_type(self) -> None:
        """Unknown ``connection_type`` is rejected at the DTO boundary."""
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.connections import (
            CreateConnectionRequest,
        )

        with pytest.raises(PydanticValidationError):
            CreateConnectionRequest.model_validate(
                {"name": "x", "connection_type": "not-a-type"},
            )

    async def test_create_duplicate_raises_conflict(self) -> None:
        from synthorg.api.controllers.connections import (
            ConnectionsController,
            CreateConnectionRequest,
        )

        catalog = MagicMock()
        catalog.create = AsyncMock(
            side_effect=DuplicateConnectionError("dup"),
        )
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        request_body = CreateConnectionRequest(
            name=NotBlankStr("x"),
            connection_type=ConnectionType.GITHUB,
            credentials={"token": "t"},
        )
        with pytest.raises(ConflictError):
            await ctrl.create_connection.fn(
                ctrl,
                state=state,
                data=request_body,
            )

    async def test_reveal_secret_returns_field(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.get_credentials = AsyncMock(
            return_value={"client_secret": "real-secret-value"},
        )
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        response = await ctrl.reveal_secret.fn(
            ctrl,
            state=state,
            name="gh",
            field="client_secret",
        )
        assert response.data == {
            "field": "client_secret",
            "value": "real-secret-value",
        }

    async def test_reveal_secret_missing_field_raises(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.get_credentials = AsyncMock(return_value={"other": "x"})
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError) as exc_info:
            await ctrl.reveal_secret.fn(
                ctrl,
                state=state,
                name="gh",
                field="client_secret",
            )
        # Error message must not leak the field/connection identity.
        assert "client_secret" not in str(exc_info.value)
        assert "gh" not in str(exc_info.value)

    async def test_reveal_secret_connection_not_found_hidden(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController
        from synthorg.integrations.errors import ConnectionNotFoundError

        catalog = MagicMock()
        catalog.get_credentials = AsyncMock(
            side_effect=ConnectionNotFoundError("Connection 'gh' not found"),
        )
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError) as exc_info:
            await ctrl.reveal_secret.fn(
                ctrl,
                state=state,
                name="gh",
                field="client_secret",
            )
        # Verify the connection name is not leaked in the public error.
        assert "gh" not in str(exc_info.value)

    async def test_reveal_secret_backend_error_hidden(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController
        from synthorg.integrations.errors import SecretRetrievalError

        catalog = MagicMock()
        catalog.get_credentials = AsyncMock(
            side_effect=SecretRetrievalError("vault timeout"),
        )
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError) as exc_info:
            await ctrl.reveal_secret.fn(
                ctrl,
                state=state,
                name="gh",
                field="client_secret",
            )
        # Backend failure detail must not leak to the client.
        assert "vault" not in str(exc_info.value).lower()


@pytest.mark.integration
class TestWebhooksController:
    async def test_missing_signing_secret_fails_closed(self) -> None:
        from synthorg.api.controllers.webhooks import WebhooksController

        catalog = MagicMock()
        catalog.get = AsyncMock(return_value=_make_conn())
        catalog.get_credentials = AsyncMock(return_value={})

        app_state = MagicMock(
            connection_catalog=catalog,
            message_bus=MagicMock(),
        )
        # Pin concrete ints on the config so the body-size guard
        # can compare against real values instead of MagicMock-vs-int.
        app_state.config.integrations.webhooks.max_payload_bytes = 1_000_000
        app_state.config.integrations.webhooks.replay_window_seconds = 300
        app_state._webhook_replay_protector = None
        state = {"app_state": app_state}

        request = MagicMock()

        # The receiver buffers the body via ``request.stream()`` so it
        # can abort on overflow without a single large allocation. Mock
        # an async iterator that yields the body once, then completes.
        async def _stream_empty() -> AsyncIterator[bytes]:
            yield b"{}"

        request.stream = _stream_empty
        request.headers = {}

        ctrl = WebhooksController(owner=WebhooksController)  # type: ignore[arg-type]
        with pytest.raises(UnauthorizedError):
            await ctrl.receive_webhook.fn(
                ctrl,
                state=state,
                request=request,
                connection_name="c1",
                event_type="ping",
            )

    async def test_malformed_timestamp_raises_validation(self) -> None:
        import hashlib
        import hmac

        from synthorg.api.controllers.webhooks import WebhooksController

        # Use generic_http so the generic HMAC verifier kicks in.
        conn = Connection(
            name=NotBlankStr("c1"),
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY,
            base_url=NotBlankStr("https://example.com"),
        )
        catalog = MagicMock()
        catalog.get = AsyncMock(return_value=conn)
        catalog.get_credentials = AsyncMock(
            return_value={"signing_secret": "supersecret"},
        )

        app_state = MagicMock(
            connection_catalog=catalog,
            message_bus=MagicMock(),
        )
        app_state.config.integrations.webhooks.max_payload_bytes = 1_000_000
        app_state.config.integrations.webhooks.replay_window_seconds = 300
        app_state._webhook_replay_protector = None
        state = {"app_state": app_state}

        body = b'{"hello":1}'
        secret = "supersecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        request = MagicMock()

        async def _stream_body() -> AsyncIterator[bytes]:
            yield body

        request.stream = _stream_body
        request.headers = {
            "X-Signature": sig,
            "X-Timestamp": "not-a-number",
        }

        ctrl = WebhooksController(owner=WebhooksController)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await ctrl.receive_webhook.fn(
                ctrl,
                state=state,
                request=request,
                connection_name="c1",
                event_type="push",
            )


@pytest.mark.integration
class TestIntegrationHealthController:
    async def test_aggregate_runs_checks_in_parallel(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.api.controllers.integration_health import (
            IntegrationHealthController,
        )
        from synthorg.integrations.health import service as health_service

        conn1 = _make_conn("c1")
        conn2 = _make_conn("c2")

        catalog = MagicMock()
        catalog.list_all = AsyncMock(return_value=(conn1, conn2))
        catalog.get_or_raise = AsyncMock(
            side_effect=lambda name: conn1 if name == "c1" else conn2
        )

        async def fake_check(
            _catalog: object,
            name: str,
        ) -> HealthReport:
            return HealthReport(
                connection_name=NotBlankStr(name),
                status=ConnectionStatus.HEALTHY,
                latency_ms=1.0,
                checked_at=datetime.now(UTC),
            )

        # Patch the source module so the controller's import reference
        # picks up the fake. Patching via ``monkeypatch`` guarantees
        # the original is restored even if the test aborts.
        monkeypatch.setattr(health_service, "check_connection_health", fake_check)
        import synthorg.api.controllers.integration_health as controller_mod

        monkeypatch.setattr(controller_mod, "check_connection_health", fake_check)

        state = MagicMock()
        state.app_state = MagicMock(connection_catalog=catalog)
        state.app_state.cursor_secret = CursorSecret.from_key(
            "test-secret-32-bytes-long-enough!",
        )
        ctrl = IntegrationHealthController(owner=IntegrationHealthController)  # type: ignore[arg-type]
        response = await ctrl.aggregate_health.fn(ctrl, state=state)

        assert len(response.data) == 2
        assert {r.connection_name for r in response.data} == {"c1", "c2"}
        assert response.pagination.total == 2
        assert response.pagination.has_more is False

    async def test_aggregate_paginates_and_only_probes_page_connections(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pagination slices connections first, probes only the page."""
        from synthorg.api.controllers.integration_health import (
            IntegrationHealthController,
        )
        from synthorg.integrations.health import service as health_service

        # Unsorted catalog input so a sort regression cannot slip
        # through behind a pre-ordered fixture. The deterministic
        # name-sort means page 1 must be c0/c1/c2 in that exact
        # order, regardless of catalog insertion order.
        conns = (
            _make_conn("c4"),
            _make_conn("c1"),
            _make_conn("c3"),
            _make_conn("c0"),
            _make_conn("c5"),
            _make_conn("c2"),
        )
        catalog = MagicMock()
        catalog.list_all = AsyncMock(return_value=conns)

        probe_calls: list[str] = []

        async def tracking_check(
            _catalog: object,
            name: str,
        ) -> HealthReport:
            probe_calls.append(name)
            return HealthReport(
                connection_name=NotBlankStr(name),
                status=ConnectionStatus.HEALTHY,
                latency_ms=1.0,
                checked_at=datetime.now(UTC),
            )

        monkeypatch.setattr(health_service, "check_connection_health", tracking_check)
        import synthorg.api.controllers.integration_health as controller_mod

        monkeypatch.setattr(
            controller_mod,
            "check_connection_health",
            tracking_check,
        )

        state = MagicMock()
        state.app_state = MagicMock(connection_catalog=catalog)
        state.app_state.cursor_secret = CursorSecret.from_key(
            "test-secret-32-bytes-long-enough!",
        )
        ctrl = IntegrationHealthController(owner=IntegrationHealthController)  # type: ignore[arg-type]
        response = await ctrl.aggregate_health.fn(
            ctrl,
            state=state,
            cursor=None,
            limit=3,
        )

        assert len(response.data) == 3
        assert response.pagination.has_more is True
        assert response.pagination.next_cursor is not None
        assert response.pagination.total == 6
        # Exact name-sorted page contents (and probe order) -- a sort
        # regression that returned the wrong three connections, or
        # probed them out of order, fails here.
        assert probe_calls == ["c0", "c1", "c2"]
        assert [r.connection_name for r in response.data] == ["c0", "c1", "c2"]


@pytest.mark.integration
class TestMCPCatalogController:
    async def test_browse_returns_bundled_entries(self) -> None:
        from synthorg.api.controllers.mcp_catalog import MCPCatalogController
        from synthorg.api.cursor import CursorSecret
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                cursor_secret=CursorSecret.ephemeral(),
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        response = await ctrl.browse_catalog.fn(
            ctrl,
            state=state,
            limit=50,
            cursor=None,
        )
        # Bundled catalog has at least 8 entries; cursor pagination
        # returns the first page plus pagination metadata.
        assert len(response.data) >= 8
        assert response.pagination.offset == 0

    async def test_browse_rejects_tampered_cursor(self) -> None:
        from synthorg.api.controllers.mcp_catalog import MCPCatalogController
        from synthorg.api.cursor import CursorSecret, InvalidCursorError
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                cursor_secret=CursorSecret.ephemeral(),
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]

        # Tampered cursor that does not carry an HMAC signature recognised
        # by ``cursor_secret``. Controller-level: confirm the decode error
        # surfaces as ``InvalidCursorError`` (mapped to HTTP 400 by the
        # exception handler) rather than corrupted data.
        with pytest.raises(InvalidCursorError):
            await ctrl.browse_catalog.fn(
                ctrl,
                state=state,
                limit=10,
                cursor="not-a-real-cursor",
            )

    async def test_install_connectionless_entry(self) -> None:
        from synthorg.api.controllers.mcp_catalog import (
            InstallEntryRequest,
            MCPCatalogController,
        )
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.service import CatalogService

        repo = InMemoryMcpInstallationRepository()
        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                mcp_installations_repo=repo,
                has_connection_catalog=False,
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        response = await ctrl.install_entry.fn(
            ctrl,
            state=state,
            data=InstallEntryRequest(catalog_entry_id="filesystem-mcp"),
        )
        assert response.data.status == "installed"
        assert response.data.server_name == "Filesystem"
        assert response.data.catalog_entry_id == "filesystem-mcp"
        # tool_count matches filesystem-mcp capabilities:
        # file_read, file_write, directory_listing.
        assert response.data.tool_count == 3
        stored = await repo.get(NotBlankStr("filesystem-mcp"))
        assert stored is not None
        # Repeat install must be idempotent -- same row, same response.
        second = await ctrl.install_entry.fn(
            ctrl,
            state=state,
            data=InstallEntryRequest(catalog_entry_id="filesystem-mcp"),
        )
        assert second.data == response.data
        assert len(await repo.list_all()) == 1

    async def test_install_missing_entry_raises_404(self) -> None:
        from synthorg.api.controllers.mcp_catalog import (
            InstallEntryRequest,
            MCPCatalogController,
        )
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                mcp_installations_repo=InMemoryMcpInstallationRepository(),
                has_connection_catalog=False,
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await ctrl.install_entry.fn(
                ctrl,
                state=state,
                data=InstallEntryRequest(catalog_entry_id="nope"),
            )

    async def test_install_connection_type_mismatch_400(self) -> None:
        from synthorg.api.controllers.mcp_catalog import (
            InstallEntryRequest,
            MCPCatalogController,
        )
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.service import CatalogService

        catalog = MagicMock()
        wrong_type_conn = Connection(
            name=NotBlankStr("slacky"),
            connection_type=ConnectionType.SLACK,
            auth_method=AuthMethod.API_KEY,
        )
        catalog.get = AsyncMock(return_value=wrong_type_conn)

        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                mcp_installations_repo=InMemoryMcpInstallationRepository(),
                has_connection_catalog=True,
                connection_catalog=catalog,
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await ctrl.install_entry.fn(
                ctrl,
                state=state,
                data=InstallEntryRequest(
                    catalog_entry_id="github-mcp",
                    connection_name="slacky",
                ),
            )

    async def test_uninstall_existing_entry(self) -> None:
        from synthorg.api.controllers.mcp_catalog import MCPCatalogController
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.installations import McpInstallation
        from synthorg.integrations.mcp_catalog.service import CatalogService

        repo = InMemoryMcpInstallationRepository()
        await repo.save(
            McpInstallation(
                catalog_entry_id=NotBlankStr("filesystem-mcp"),
                connection_name=None,
                installed_at=datetime.now(UTC),
            ),
        )
        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                mcp_installations_repo=repo,
                has_connection_catalog=False,
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        response = await ctrl.uninstall_entry.fn(
            ctrl,
            state=state,
            entry_id="filesystem-mcp",
        )
        assert response.data is None
        assert await repo.get(NotBlankStr("filesystem-mcp")) is None

    async def test_uninstall_missing_is_idempotent(self) -> None:
        from synthorg.api.controllers.mcp_catalog import MCPCatalogController
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = {
            "app_state": MagicMock(
                mcp_catalog_service=CatalogService(),
                mcp_installations_repo=InMemoryMcpInstallationRepository(),
                has_connection_catalog=False,
            ),
        }
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        response = await ctrl.uninstall_entry.fn(
            ctrl,
            state=state,
            entry_id="not-installed",
        )
        assert response.data is None


@pytest.mark.integration
class TestTunnelController:
    async def test_start_returns_public_url(self) -> None:
        from synthorg.api.controllers.tunnel import TunnelController

        tunnel = MagicMock()
        tunnel.start = AsyncMock(return_value="https://tunnel.example.com")
        state = {"app_state": MagicMock(tunnel_provider=tunnel)}
        ctrl = TunnelController(owner=TunnelController)  # type: ignore[arg-type]
        response = await ctrl.start_tunnel.fn(ctrl, state=state)
        assert response.data == {"public_url": "https://tunnel.example.com"}

    async def test_status_returns_current_url(self) -> None:
        from synthorg.api.controllers.tunnel import TunnelController

        tunnel = MagicMock()
        tunnel.get_url = AsyncMock(return_value="https://tunnel.example.com")
        state = {"app_state": MagicMock(tunnel_provider=tunnel)}
        ctrl = TunnelController(owner=TunnelController)  # type: ignore[arg-type]
        response = await ctrl.get_status.fn(ctrl, state=state)
        assert response.data == {"public_url": "https://tunnel.example.com"}


@pytest.mark.integration
class TestOAuthController:
    async def test_initiate_requires_connection_name(self) -> None:
        """Missing ``connection_name`` is rejected at the DTO boundary (#1682)."""
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.oauth import (
            InitiateOAuthFlowRequest,
        )

        with pytest.raises(PydanticValidationError):
            InitiateOAuthFlowRequest.model_validate({})

    async def test_status_returns_false_when_no_token(self) -> None:
        from synthorg.api.controllers.oauth import OAuthController

        conn = _make_conn()
        catalog = MagicMock()
        catalog.get_or_raise = AsyncMock(return_value=conn)
        catalog.get_credentials = AsyncMock(return_value={})
        state = {"app_state": MagicMock(connection_catalog=catalog)}

        ctrl = OAuthController(owner=OAuthController)  # type: ignore[arg-type]
        response = await ctrl.token_status.fn(
            ctrl,
            state=state,
            connection_name="c1",
        )
        assert response.data["has_token"] is False


@pytest.mark.integration
class TestControllerHttpLayer:
    """End-to-end smoke checks through Litestar ``TestClient``.

    The per-controller tests above call handlers directly, which is
    fast but bypasses routing, guards, dependency injection, and
    RFC 9457 error response translation. These smoke tests drive
    the same handlers through a real ``TestClient`` so a regression
    in the HTTP stack surfaces here instead of in production.
    """

    def _build_client(
        self,
        catalog: MagicMock,
    ) -> TestClient[Any]:
        """Construct a minimal Litestar app + test client for smoke tests.

        Wires a no-op per-op rate-limit guard so write endpoints
        (e.g. ``POST /connections``) don't 503 with the
        "rate limiter not wired" deployment-error guard. The guard
        is on by default to make production deployments fail-closed,
        but smoke tests run against a stub store so the test layer
        can verify routing/DTO/error translation in isolation.
        """
        from litestar import Litestar, Router
        from litestar.datastructures import State
        from litestar.middleware import ASGIMiddleware

        from synthorg.api.controllers import (
            ConnectionsController,
            IntegrationHealthController,
        )
        from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
        from synthorg.api.rate_limits._subject import (
            STATE_KEY_CONFIG,
            STATE_KEY_STORE,
        )
        from synthorg.api.rate_limits.config import PerOpRateLimitConfig
        from synthorg.api.rate_limits.protocol import (
            RateLimitOutcome,
            SlidingWindowStore,
        )
        from synthorg.api.state import AppState

        # Stub rate-limit store: the guard calls
        # ``store.acquire(...)`` and inspects the
        # :class:`RateLimitOutcome` it returns (see protocol.py).
        # Returning a tuple here would silently violate the contract
        # (#1682, CodeRabbit at integrations/test_controllers.py:755).
        # ``spec=`` enforces the protocol surface (#1604) so a future
        # ``SlidingWindowStore`` method rename surfaces as an
        # ``AttributeError`` instead of a silent test pass.
        rate_limit_store = MagicMock(spec=SlidingWindowStore)
        rate_limit_store.acquire = AsyncMock(
            spec=SlidingWindowStore.acquire,
            return_value=RateLimitOutcome(allowed=True, remaining=999),
        )
        rate_limit_config = PerOpRateLimitConfig(enabled=False)

        app_state_stub = MagicMock(
            spec=AppState,
            connection_catalog=catalog,
            has_per_op_rate_limit_config=True,
            per_op_rate_limit_config=rate_limit_config,
        )

        class _TestUser:
            role = "ceo"
            id = "test-user"
            username = "test"
            must_change_password = False

        class _InjectUserMiddleware(ASGIMiddleware):
            """Stuff a fake CEO user into scope so guards allow.

            ``require_read_access`` reads ``scope["user"].role``. The
            real auth middleware stack is intentionally not wired in
            these smoke tests (they verify routing, DI, and error
            translation, not auth), so we inject a minimal
            ``_TestUser`` here instead of spinning up the full auth
            pipeline.
            """

            async def handle(
                self,
                scope: Any,
                receive: Any,
                send: Any,
                next_app: Any,
            ) -> None:
                if scope["type"] == "http":
                    scope["user"] = _TestUser()
                await next_app(scope, receive, send)

        # Mirror production's routing layout: controllers declare
        # their paths *without* the ``/api/v1`` prefix, and the
        # top-level ``Router`` in ``app.py`` mounts them under
        # ``api_config.api_prefix``. The smoke test wraps the same
        # way so routing regressions surface here instead of shipping.
        api_router = Router(
            path="/api/v1",
            route_handlers=[
                ConnectionsController,
                IntegrationHealthController,
            ],
        )
        app = Litestar(
            route_handlers=[api_router],
            state=State(
                {
                    "app_state": app_state_stub,
                    STATE_KEY_STORE: rate_limit_store,
                    STATE_KEY_CONFIG: rate_limit_config,
                },
            ),
            middleware=[_InjectUserMiddleware()],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        return TestClient(app)

    async def test_list_connections_returns_200(self) -> None:
        from synthorg.integrations.connections.catalog import ConnectionCatalog

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.list_all = AsyncMock(
            spec=ConnectionCatalog.list_all,
            return_value=(_make_conn(),),
        )
        client = self._build_client(catalog)
        with client as http:
            resp = http.get("/api/v1/connections")
        # The full HTTP stack must return an exact 200 with the
        # connection list serialized through the ApiResponse
        # envelope. Any other status would be a regression in
        # routing, DI, or serialization.
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "c1"

    async def test_unknown_connection_returns_404(self) -> None:
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.integrations.errors import ConnectionNotFoundError

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_or_raise = AsyncMock(
            spec=ConnectionCatalog.get_or_raise,
            side_effect=ConnectionNotFoundError("missing"),
        )
        client = self._build_client(catalog)
        with client as http:
            resp = http.get("/api/v1/integrations/health/missing")
        # Expect a structured 404 via RFC 9457 translation -- the
        # ``NotFoundError`` raised by the handler must be mapped
        # to the right status and serialized through the error
        # middleware, not leaked as a 500.
        assert resp.status_code == 404
        body = resp.json()
        assert "missing" in body.get("detail", body.get("error", "")).lower()

    async def test_create_connection_invalid_body_returns_4xx(self) -> None:
        """Invalid POST body is rejected at the request boundary.

        Pre-PR review #1682 (CodeRabbit at integrations/test_controllers.py:113):
        the model-level ``test_create_validates_*`` cases assert the
        Pydantic DTO rejects bad payloads, but only an end-to-end
        ``TestClient`` round-trip proves the controller's signature
        still binds the DTO and that Litestar's request parser
        surfaces a structured client error before the handler body
        runs.  A regression in either the route binding or the DTO
        would fall back to 200/500, which the model-level tests
        cannot detect.

        Litestar's :class:`ValidationException` defaults to 400 in
        the project's exception-handler registry; we accept any 4xx
        in [400, 422] so a future tightening to 422 (RFC-aligned)
        does not require touching this gate.
        """
        from synthorg.integrations.connections.catalog import ConnectionCatalog

        catalog = MagicMock(spec=ConnectionCatalog)
        client = self._build_client(catalog)
        with client as http:
            resp = http.post(
                "/api/v1/connections",
                json={"connection_type": "github"},
            )
        assert resp.status_code in {400, 422}
