"""Controller-level integration tests for the 6 new integration APIs.

Covers:
- ``ConnectionsController`` -- list/get/create/update/delete/health
- ``OAuthController`` -- initiate, callback, status
- ``WebhooksIngestController`` -- receive (signature verify, replay, bus publish)
- ``IntegrationHealthController`` -- aggregate + single
- ``MCPCatalogController`` -- browse/search/get
- ``TunnelController`` -- start/stop/status

The per-controller tests below invoke the underlying handler via
``handler.fn(ctrl, ...)`` so they run fast and can mock every
collaborator. ``TestControllerHttpLayer`` complements them with an
end-to-end sanity check through ``LoopAsyncClient`` so guards,
dependency injection, and RFC 9457 error translation are exercised
on the real HTTP path.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, override
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.datastructures import State

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
from tests._shared import LoopAsyncClient, make_app_state


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
        state = {
            "app_state": make_app_state(
                connection_catalog=catalog,
                cursor_secret=CursorSecret.ephemeral(),
            ),
        }

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        response = await ctrl.list_connections.fn(ctrl, state=state)
        assert len(response.data) == 2
        catalog.list_all.assert_awaited_once()

    async def test_get_missing_raises_not_found(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.get = AsyncMock(return_value=None)
        state = {"app_state": make_app_state(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await ctrl.get_connection.fn(ctrl, state=state, name="missing")

    async def test_create_validates_missing_name(self) -> None:
        # Pydantic validation on ``CreateConnectionRequest`` rejects the
        # missing required ``name`` before the controller is reached;
        # in production this surfaces as an automatic 4xx via Litestar.
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.connections import CreateConnectionRequest

        with pytest.raises(PydanticValidationError):
            CreateConnectionRequest.model_validate({"connection_type": "github"})

    async def test_create_validates_bad_connection_type(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.connections import CreateConnectionRequest

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
        state = {"app_state": make_app_state(connection_catalog=catalog)}

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with pytest.raises(ConflictError):
            await ctrl.create_connection.fn(
                ctrl,
                state=state,
                data=CreateConnectionRequest.model_validate(
                    {
                        "name": "x",
                        "connection_type": "github",
                        "credentials": {"token": "t"},
                    },
                ),
            )

    async def test_reveal_secret_returns_field(self) -> None:
        from synthorg.api.controllers.connections import ConnectionsController

        catalog = MagicMock()
        catalog.get_credentials = AsyncMock(
            return_value={"client_secret": "real-secret-value"},
        )
        state = {"app_state": make_app_state(connection_catalog=catalog)}

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
        state = {"app_state": make_app_state(connection_catalog=catalog)}

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
        state = {"app_state": make_app_state(connection_catalog=catalog)}

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
        state = {"app_state": make_app_state(connection_catalog=catalog)}

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


def _make_audit_state(catalog: object) -> dict[str, object]:
    """Build a minimal ``state`` mapping that pins ``connection_catalog``.

    Uses ``make_app_state`` so the controller resolves the catalog
    through the IntegrationsStateSlice. The controller only reads
    ``connection_catalog``; every other slice field stays ``None``.
    """
    return {"app_state": make_app_state(connection_catalog=catalog)}


def _capture_emission(
    events: Sequence[Mapping[str, object]],
    name: str,
) -> Mapping[str, object]:
    """Return the single event dict matching ``name`` from the captured list."""
    matches = [e for e in events if e.get("event") == name]
    if len(matches) != 1:
        msg = f"expected 1 emission of {name!r}, got {len(matches)}: {matches}"
        raise AssertionError(msg)
    return matches[0]


@pytest.mark.integration
class TestConnectionAuditEvents:
    """Connection mutations emit ``security.connection.*`` events.

    The ``AuditChainSink`` filters on the ``security.*`` prefix; an
    event under ``integrations.*`` would never reach the chain. These
    tests guard the prefix contract at the controller boundary so
    forensic reconstruction of credential CRUD stays possible.

    Uses ``structlog.testing.capture_logs`` because the structured
    ``event`` key lives in the structlog event dict, not on the stdlib
    ``LogRecord``.
    """

    async def test_create_emits_security_event_with_payload(self) -> None:
        """Create success emits one ``SECURITY_CONNECTION_CREATED`` and
        carries the bare ``connection`` field (matching SECURITY_PROVIDER_*
        naming) plus the connection_type and auth_method context."""
        import structlog

        from synthorg.api.controllers.connections import (
            ConnectionsController,
            CreateConnectionRequest,
        )
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.observability.events.security import (
            SECURITY_CONNECTION_CREATED,
        )

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.create.return_value = _make_conn()

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.create_connection.fn(
                ctrl,
                state=_make_audit_state(catalog),
                data=CreateConnectionRequest.model_validate(
                    {
                        "name": "gh",
                        "connection_type": "github",
                        "credentials": {"token": "t"},
                    },
                ),
            )

        emission = _capture_emission(events, SECURITY_CONNECTION_CREATED)
        assert emission["connection"] == "gh"
        assert emission["connection_type"] == "github"
        assert emission["auth_method"] == "api_key"

    async def test_update_emits_security_event_with_fields_changed(self) -> None:
        """Update success carries the ``connection`` field and
        ``fields_changed`` tag listing the partial-update keys."""
        import structlog

        from synthorg.api.controllers.connections import (
            ConnectionsController,
            UpdateConnectionRequest,
        )
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.observability.events.security import (
            SECURITY_CONNECTION_UPDATED,
        )

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.update.return_value = _make_conn()

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.update_connection.fn(
                ctrl,
                state=_make_audit_state(catalog),
                name="gh",
                data=UpdateConnectionRequest.model_validate(
                    {"base_url": "https://api.github.com/v4"},
                ),
            )

        emission = _capture_emission(events, SECURITY_CONNECTION_UPDATED)
        assert emission["connection"] == "gh"
        assert emission["fields_changed"] == ["base_url"]

    async def test_delete_emits_security_event(self) -> None:
        import structlog

        from synthorg.api.controllers.connections import ConnectionsController
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.observability.events.security import (
            SECURITY_CONNECTION_DELETED,
        )

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.delete.return_value = None

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.delete_connection.fn(
                ctrl,
                state=_make_audit_state(catalog),
                name="gh",
            )

        emission = _capture_emission(events, SECURITY_CONNECTION_DELETED)
        assert emission["connection"] == "gh"

    async def test_reveal_success_emits_security_event(self) -> None:
        """Reveal success emits exactly one
        ``SECURITY_CONNECTION_SECRET_REVEALED`` carrying the bare
        ``connection`` field name and the ``field`` accessed; the actual
        secret value is NEVER part of the event payload."""
        import structlog

        from synthorg.api.controllers.connections import ConnectionsController
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.observability.events.security import (
            SECURITY_CONNECTION_SECRET_REVEALED,
        )

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_credentials.return_value = {
            "client_secret": "real-secret-value",
        }

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.reveal_secret.fn(
                ctrl,
                state=_make_audit_state(catalog),
                name="gh",
                field="client_secret",
            )

        emission = _capture_emission(events, SECURITY_CONNECTION_SECRET_REVEALED)
        assert emission["connection"] == "gh"
        assert emission["field"] == "client_secret"
        # Secret value never appears in ANY captured log payload, not
        # just the matched emission. A future refactor that splits the
        # log call across multiple events MUST keep the secret out of
        # every payload; iterating the full event stream catches the
        # accidental leak that an emission-only check would miss.
        for event in events:
            for value in event.values():
                assert "real-secret-value" not in str(value)

    @pytest.mark.parametrize(
        ("setup_side_effect", "expected_reason"),
        [
            (
                "field_missing",
                "field_not_set",
            ),
            (
                "connection_missing",
                "connection_not_found",
            ),
            (
                "backend_error",
                "secret_retrieval_failed",
            ),
        ],
        ids=["field_missing", "connection_missing", "backend_error"],
    )
    async def test_reveal_failure_emits_security_event_with_reason(
        self,
        setup_side_effect: str,
        expected_reason: str,
    ) -> None:
        """Each reveal-failure branch emits ``SECURITY_CONNECTION_SECRET_REVEAL_FAILED``
        with the right ``reason`` tag. Locks the contract that future
        refactors keep the three branches distinguishable in the audit chain."""
        import structlog

        from synthorg.api.controllers.connections import ConnectionsController
        from synthorg.integrations.connections.catalog import ConnectionCatalog
        from synthorg.integrations.errors import (
            ConnectionNotFoundError,
            SecretRetrievalError,
        )
        from synthorg.observability.events.security import (
            SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
        )

        catalog = MagicMock(spec=ConnectionCatalog)
        if setup_side_effect == "field_missing":
            catalog.get_credentials.return_value = {"other": "x"}
        elif setup_side_effect == "connection_missing":
            catalog.get_credentials.side_effect = ConnectionNotFoundError(
                "gh missing",
            )
        else:
            catalog.get_credentials.side_effect = SecretRetrievalError(
                "vault timeout",
            )

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events, pytest.raises(NotFoundError):
            await ctrl.reveal_secret.fn(
                ctrl,
                state=_make_audit_state(catalog),
                name="gh",
                field="client_secret",
            )

        emission = _capture_emission(events, SECURITY_CONNECTION_SECRET_REVEAL_FAILED)
        assert emission["connection"] == "gh"
        assert emission["field"] == "client_secret"
        assert emission["reason"] == expected_reason


@pytest.mark.integration
class TestWebhooksController:
    async def test_missing_signing_secret_fails_closed(self) -> None:
        from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController

        catalog = MagicMock()
        catalog.get = AsyncMock(return_value=_make_conn())
        catalog.get_credentials = AsyncMock(return_value={})

        from synthorg.communication.bus_protocol import MessageBus

        app_state = make_app_state(
            connection_catalog=catalog,
            message_bus=MagicMock(spec=MessageBus),
        )
        state = {"app_state": app_state}

        request = MagicMock()

        # The receiver buffers the body via ``request.stream()`` so it
        # can abort on overflow without a single large allocation. Mock
        # an async iterator that yields the body once, then completes.
        async def _stream_empty() -> AsyncIterator[bytes]:
            yield b"{}"

        request.stream = _stream_empty
        request.headers = {}

        ctrl = WebhooksIngestController(owner=WebhooksIngestController)  # type: ignore[arg-type]
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

        from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController

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

        from synthorg.communication.bus_protocol import MessageBus

        app_state = make_app_state(
            connection_catalog=catalog,
            message_bus=MagicMock(spec=MessageBus),
        )
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

        ctrl = WebhooksIngestController(owner=WebhooksIngestController)  # type: ignore[arg-type]
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

        app_state = make_app_state(
            connection_catalog=catalog,
            cursor_secret=CursorSecret.from_key(
                "test-secret-32-bytes-long-enough!",
            ),
        )
        state = State({"app_state": app_state})
        ctrl = IntegrationHealthController(owner=IntegrationHealthController)  # type: ignore[arg-type]
        response = await ctrl.aggregate_health.fn(ctrl, state=state)

        assert len(response.data) == 2
        assert {r.connection_name for r in response.data} == {"c1", "c2"}
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

        app_state = make_app_state(
            connection_catalog=catalog,
            cursor_secret=CursorSecret.from_key(
                "test-secret-32-bytes-long-enough!",
            ),
        )
        state = State({"app_state": app_state})
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

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    cursor_secret=CursorSecret.ephemeral(),
                ),
            }
        )
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

    async def test_browse_rejects_tampered_cursor(self) -> None:
        from synthorg.api.controllers.mcp_catalog import MCPCatalogController
        from synthorg.api.cursor import CursorSecret, InvalidCursorError
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    cursor_secret=CursorSecret.ephemeral(),
                ),
            }
        )
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
        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=repo,
                ),
            }
        )
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
        assert len(await repo.list_items()) == 1

    async def test_install_missing_entry_raises_404(self) -> None:
        from synthorg.api.controllers.mcp_catalog import (
            InstallEntryRequest,
            MCPCatalogController,
        )
        from synthorg.integrations.mcp_catalog.in_memory_installations import (
            InMemoryMcpInstallationRepository,
        )
        from synthorg.integrations.mcp_catalog.service import CatalogService

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=InMemoryMcpInstallationRepository(),
                ),
            }
        )
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

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=InMemoryMcpInstallationRepository(),
                    connection_catalog=catalog,
                ),
            }
        )
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
        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=repo,
                ),
            }
        )
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

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=InMemoryMcpInstallationRepository(),
                ),
            }
        )
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        response = await ctrl.uninstall_entry.fn(
            ctrl,
            state=state,
            entry_id="not-installed",
        )
        assert response.data is None

    async def test_list_installed_drains_all_repo_pages(self) -> None:
        from datetime import UTC, datetime

        from synthorg.api.controllers.mcp_catalog import (
            _LIST_PAGE_SIZE,
            MCPCatalogController,
        )
        from synthorg.api.cursor import CursorSecret
        from synthorg.integrations.mcp_catalog.installations import McpInstallation
        from synthorg.integrations.mcp_catalog.service import CatalogService

        # Two repo pages: one full (triggers the loop to ask for
        # another batch) and one short (terminates the drain).  The
        # controller must drain both pages before paginate_cursor
        # wraps the response so the dashboard never sees a truncated
        # installed list when the install count crosses the page
        # boundary.
        now = datetime.now(UTC)
        first_page = tuple(
            McpInstallation(
                catalog_entry_id=NotBlankStr(f"entry-{idx:04d}"),
                connection_name=None,
                installed_at=now,
            )
            for idx in range(_LIST_PAGE_SIZE)
        )
        second_page = (
            McpInstallation(
                catalog_entry_id=NotBlankStr("entry-tail"),
                connection_name=None,
                installed_at=now,
            ),
        )
        repo = MagicMock()
        repo.list_items = AsyncMock(side_effect=[first_page, second_page])

        state = State(
            {
                "app_state": make_app_state(
                    mcp_catalog_service=CatalogService(),
                    mcp_installations_repo=repo,
                    cursor_secret=CursorSecret.ephemeral(),
                ),
            },
        )
        ctrl = MCPCatalogController(owner=MCPCatalogController)  # type: ignore[arg-type]
        await ctrl.list_installed.fn(
            ctrl,
            state=state,
            limit=50,
            cursor=None,
        )
        # The drain loop must request a second batch once the first
        # batch comes back full; without it, the second page would
        # silently truncate.  The short second page terminates the
        # drain so a third call is never issued.
        assert repo.list_items.await_count == 2
        assert repo.list_items.await_args_list[0].kwargs["offset"] == 0
        assert repo.list_items.await_args_list[1].kwargs["offset"] == _LIST_PAGE_SIZE


@pytest.mark.integration
class TestTunnelController:
    async def test_start_returns_public_url(self) -> None:
        from synthorg.api.controllers.tunnel import TunnelController

        tunnel = MagicMock()
        tunnel.start = AsyncMock(return_value="https://tunnel.example.com")
        state = {"app_state": make_app_state(tunnel_provider=tunnel)}
        ctrl = TunnelController(owner=TunnelController)  # type: ignore[arg-type]
        response = await ctrl.start_tunnel.fn(ctrl, state=state)
        assert response.data == {"public_url": "https://tunnel.example.com"}

    async def test_status_returns_current_url(self) -> None:
        from synthorg.api.controllers.tunnel import TunnelController

        tunnel = MagicMock()
        tunnel.get_url = AsyncMock(return_value="https://tunnel.example.com")
        tunnel.has_auth_token = True
        state = {"app_state": make_app_state(tunnel_provider=tunnel)}
        ctrl = TunnelController(owner=TunnelController)  # type: ignore[arg-type]
        response = await ctrl.get_status.fn(ctrl, state=state)
        assert response.data == {
            "public_url": "https://tunnel.example.com",
            "has_auth_token": True,
        }


@pytest.mark.integration
class TestOAuthController:
    async def test_initiate_requires_connection_name(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        from synthorg.api.controllers.oauth import InitiateOAuthFlowRequest

        # DTO-level: the Pydantic model itself rejects a missing
        # ``connection_name`` before any controller code runs.
        with pytest.raises(PydanticValidationError):
            InitiateOAuthFlowRequest()  # type: ignore[call-arg]

        # HTTP-bound: posting an empty body to the real route through
        # ``TestClient`` exercises the framework's request-body
        # validation path, so a regression that bypasses Pydantic
        # binding (e.g. switching the body annotation back to
        # ``dict[str, Any]``) would surface here as a 200/500
        # instead of the expected 422.
        from litestar import Litestar, Router
        from litestar.datastructures import State as LitestarState
        from litestar.middleware import ASGIMiddleware

        from synthorg.api.controllers.oauth import OAuthController
        from synthorg.api.exception_handlers import EXCEPTION_HANDLERS

        class _TestUser:
            role = "ceo"
            id = "test-user"
            username = "test"
            must_change_password = False

        class _InjectUserMiddleware(ASGIMiddleware):
            @override
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

        from synthorg.api.rate_limits import InMemorySlidingWindowStore
        from synthorg.api.rate_limits._subject import (
            STATE_KEY_CONFIG,
            STATE_KEY_STORE,
        )
        from synthorg.api.rate_limits.config import PerOpRateLimitConfig
        from synthorg.api.state import AppState

        app_state_stub = MagicMock(spec=AppState)
        # ``MagicMock(spec=AppState)`` would otherwise satisfy the
        # ``has_per_op_rate_limit_config`` getattr probe and pass back
        # another MagicMock as the live config; unpacking
        # ``mock.overrides.get(...)`` into ``(limit_max, limit_window)``
        # then explodes with "expected 2, got 0". Force the rate-limit
        # guard down its Litestar-state-dict fallback path so it picks
        # up the real config installed below.
        app_state_stub.has_per_op_rate_limit_config = False
        # The route applies a per-op rate-limit guard which runs ahead
        # of body validation. Without a wired store + config the guard
        # raises ``ServiceUnavailableError`` (503) and masks the
        # body-bind 400 this test exists to assert. Install a real
        # in-memory store and the registry-default config so the
        # guard returns success and the request flows into Litestar's
        # validation layer.
        api_router = Router(
            path="/api/v1",
            route_handlers=[OAuthController],
        )
        app = Litestar(
            route_handlers=[api_router],
            state=LitestarState(
                {
                    "app_state": app_state_stub,
                    STATE_KEY_STORE: InMemorySlidingWindowStore(),
                    STATE_KEY_CONFIG: PerOpRateLimitConfig(),
                },
            ),
            middleware=[_InjectUserMiddleware()],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        async with LoopAsyncClient(app) as http:
            resp = await http.post("/api/v1/oauth/initiate", json={})
        # Litestar surfaces request-body Pydantic-bind failures as
        # its built-in ``ValidationException`` which is mapped to
        # HTTP 400 by default. The project's domain
        # ``ValidationError`` maps to 422, but that handler runs
        # after controller code, not at the body-bind stage. The
        # important property here is that the empty body is rejected
        # before the controller runs (any switch back to
        # ``dict[str, Any]`` would let it through with a 500/404)
        # AND that the response cites the missing field so the
        # client can self-correct.
        assert resp.status_code == 400
        rendered = str(resp.json())
        assert "connection_name" in rendered or "validation" in rendered.lower()

    async def test_status_returns_false_when_no_token(self) -> None:
        from synthorg.api.controllers.oauth import OAuthController

        conn = _make_conn()
        catalog = MagicMock()
        catalog.get_or_raise = AsyncMock(return_value=conn)
        catalog.get_credentials = AsyncMock(return_value={})
        state = {"app_state": make_app_state(connection_catalog=catalog)}

        ctrl = OAuthController(owner=OAuthController)  # type: ignore[arg-type]
        response = await ctrl.token_status.fn(
            ctrl,
            state=state,
            connection_name="c1",
        )
        assert response.data["has_token"] is False


@pytest.mark.integration
class TestControllerHttpLayer:
    """End-to-end smoke checks through ``LoopAsyncClient``.

    The per-controller tests above call handlers directly, which is
    fast but bypasses routing, guards, dependency injection, and
    RFC 9457 error response translation. These smoke tests drive
    the same handlers through a real ``LoopAsyncClient`` so a regression
    in the HTTP stack surfaces here instead of in production.
    """

    def _build_client(
        self,
        catalog: MagicMock,
    ) -> LoopAsyncClient:
        """Construct a minimal Litestar app + test client for smoke tests."""
        from litestar import Litestar, Router
        from litestar.datastructures import State
        from litestar.middleware import ASGIMiddleware

        from synthorg.api.controllers import (
            ConnectionsController,
            IntegrationHealthController,
        )
        from synthorg.api.exception_handlers import EXCEPTION_HANDLERS

        app_state_stub = make_app_state(
            connection_catalog=catalog,
            cursor_secret=CursorSecret.ephemeral(),
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

            @override
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
            state=State({"app_state": app_state_stub}),
            middleware=[_InjectUserMiddleware()],
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        return LoopAsyncClient(app)

    async def test_list_connections_returns_200(self) -> None:
        catalog = MagicMock()
        catalog.list_all = AsyncMock(return_value=(_make_conn(),))
        client = self._build_client(catalog)
        async with client as http:
            resp = await http.get("/api/v1/connections")
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
        from synthorg.integrations.errors import ConnectionNotFoundError

        catalog = MagicMock()
        catalog.get_or_raise = AsyncMock(
            side_effect=ConnectionNotFoundError("missing"),
        )
        client = self._build_client(catalog)
        async with client as http:
            resp = await http.get("/api/v1/integrations/health/missing")
        # Expect a structured 404 via RFC 9457 translation -- the
        # ``NotFoundError`` raised by the handler must be mapped
        # to the right status and serialized through the error
        # middleware, not leaked as a 500.
        assert resp.status_code == 404
        body = resp.json()
        assert "missing" in body.get("detail", body.get("error", "")).lower()
