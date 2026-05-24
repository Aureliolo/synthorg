"""Path-parameter length validation on integration controllers.

The connections, mcp_catalog, oauth, and webhooks controllers expose
path parameters that flow into database queries, secret lookups, and
external-facing webhook routes.  ``PathName`` / ``PathId`` /
``PathField`` / ``PathEventType`` aliases (``synthorg.api.path_params``)
each carry ``Parameter(min_length=1, max_length=...)`` so an
attacker-controllable identifier cannot reach the persistence layer
unbounded.

These tests drive the real Litestar route through ``TestClient`` so a
regression that switches a typed alias back to bare ``str`` (or
removes the bound) surfaces here, not in production.

Run targets a 200-character path component on every covered endpoint
and asserts the framework rejects it with a 4xx (the alias caps at 64
or 128 depending on slot).  The typed alias is the only line of
defence at the edge for these endpoints.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar import Litestar, Router
from litestar.datastructures import State
from litestar.middleware import ASGIMiddleware
from litestar.testing import TestClient

from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.rate_limits._subject import STATE_KEY_CONFIG, STATE_KEY_STORE
from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from synthorg.api.state import AppState
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.mcp_catalog.service import CatalogService
from synthorg.persistence.protocol import PersistenceBackend

# Long-enough path component to overshoot every alias bound.  The
# tightest cap is ``PathEventType`` at 64 chars; 200 is past every
# ``max_length`` the path-param aliases declare.
_OVER_128_CHARS = "y" * 200


class _TestUser:
    """Stub auth principal so ``require_read_access`` lets the request through."""

    role = "ceo"
    id = "test-user"
    username = "test"
    must_change_password = False


class _InjectUserMiddleware(ASGIMiddleware):
    """Inject a CEO-role user into the ASGI scope.

    The path-param validation under test is the Litestar
    ``Parameter(min_length=..., max_length=...)`` constraint and runs
    BEFORE guards, but ``ConnectionsController`` and friends declare
    ``require_read_access`` which would otherwise reject any unauthed
    request with 401 and mask the 4xx we are trying to assert.
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


def _build_client(
    *,
    catalog: MagicMock | None = None,
    mcp_service: MagicMock | None = None,
    persistence: MagicMock | None = None,
) -> TestClient[Any]:
    """Build a minimal Litestar app + client wired with the four controllers.

    Each test passes only the collaborator(s) it needs; the rest stay
    as ``None`` to keep the construction cheap.  The 4xx the test
    asserts fires at request-binding time (before any handler body
    runs), so missing collaborators do not matter for the assertion.
    """
    from synthorg.api.controllers.connections import ConnectionsController
    from synthorg.api.controllers.mcp_catalog import MCPCatalogController
    from synthorg.api.controllers.oauth import OAuthController
    from synthorg.api.controllers.webhooks import WebhooksController

    # spec=AppState restricts attribute access to the production
    # AppState surface so the test's stub cannot drift if the real
    # state class adds, renames, or removes a field. The collaborator
    # stubs each carry a concrete spec so a rename/removal of any
    # method on the underlying interface fails this test instead of
    # silently absorbing the attribute access (mock-spec gate #1604).
    from synthorg.api.cursor import CursorSecret
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.config.schema import RootConfig
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )

    app_state_stub = MagicMock(
        spec=AppState,
        connection_catalog=catalog,
        mcp_catalog_service=mcp_service,
        mcp_installations_repo=MagicMock(spec=McpInstallationRepository),
        persistence=persistence,
        message_bus=MagicMock(spec=MessageBus),
        cursor_secret=MagicMock(spec=CursorSecret),
        config=MagicMock(spec=RootConfig),
    )

    api_router = Router(
        path="/api/v1",
        route_handlers=[
            ConnectionsController,
            MCPCatalogController,
            OAuthController,
            WebhooksController,
        ],
    )
    # ``WebhooksController`` mounts ``per_op_rate_limit_from_policy``
    # guards that read the live config + store from Litestar state
    # under the canonical keys (set by ``app.py`` in production).
    # Without these the guard fails 503 BEFORE Litestar runs path-param
    # validation, masking the 4xx the tests are asserting.  Wire the
    # in-memory store + an enabled config so the guard becomes a real
    # no-op and validation runs as expected.
    state = State({"app_state": app_state_stub})
    state[STATE_KEY_STORE] = InMemorySlidingWindowStore()
    state[STATE_KEY_CONFIG] = PerOpRateLimitConfig(enabled=True)
    app = Litestar(
        route_handlers=[api_router],
        state=state,
        middleware=[_InjectUserMiddleware()],
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )
    return TestClient(app)


@pytest.fixture
def path_param_client() -> Iterator[TestClient[Any]]:
    """Build the test client once per test."""
    # Each AsyncMock declares the unbound method as its spec so the
    # gate (scripts/check_mock_spec.py) sees a typed contract and a
    # rename or signature change in the real class fails this test.
    catalog = MagicMock(spec=ConnectionCatalog)
    catalog.get = AsyncMock(spec=ConnectionCatalog.get, return_value=None)
    catalog.get_or_raise = AsyncMock(
        spec=ConnectionCatalog.get_or_raise,
        side_effect=Exception("never reached"),
    )
    catalog.get_credentials = AsyncMock(
        spec=ConnectionCatalog.get_credentials, return_value={}
    )
    mcp_service = MagicMock(spec=CatalogService)
    mcp_service.get_entry = AsyncMock(
        spec=CatalogService.get_entry,
        side_effect=Exception("never reached"),
    )
    mcp_service.uninstall = AsyncMock(spec=CatalogService.uninstall, return_value=False)
    persistence = MagicMock(spec=PersistenceBackend)
    # PersistenceBackend.webhook_receipts is a Protocol attribute (not a
    # method), so spec=PersistenceBackend gives ``webhook_receipts`` a
    # bare child mock; replace with an explicit one whose
    # ``get_by_connection`` is a typed AsyncMock for the same gate
    # reason as the catalog methods above.
    from synthorg.persistence.connection_protocol import WebhookReceiptRepository

    persistence.webhook_receipts = MagicMock(spec=WebhookReceiptRepository)
    persistence.webhook_receipts.get_by_connection = AsyncMock(
        spec=WebhookReceiptRepository.get_by_connection, return_value=()
    )
    client = _build_client(
        catalog=catalog,
        mcp_service=mcp_service,
        persistence=persistence,
    )
    with client as http:
        yield http


@pytest.mark.integration
class TestConnectionsPathParams:
    """``connections.py`` -- typed path params on the 5 GET endpoints."""

    def test_get_connection_rejects_oversized_name(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(f"/api/v1/connections/{_OVER_128_CHARS}")
        # 400 (Bad Request) or 422 (Unprocessable Entity) -- both signal
        # the framework rejected the input before the handler ran.
        assert resp.status_code in (400, 422), resp.text

    def test_oversized_name_with_valid_query_string_still_rejects(
        self, path_param_client: TestClient[Any]
    ) -> None:
        """Path-param validation is independent of the query string.

        Without this assertion, a future framework regression where a
        clean query string accidentally short-circuits path-param
        binding (or vice-versa: where a query string influences
        path-param parsing) would slip past the bare-path tests above.
        The handler must still reject the oversized path segment even
        when sibling query params are well-formed.
        """
        resp = path_param_client.get(
            f"/api/v1/connections/{_OVER_128_CHARS}",
            params={"action": "retrieve"},
        )
        assert resp.status_code in (400, 422), resp.text

    def test_check_health_rejects_oversized_name(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/connections/{_OVER_128_CHARS}/health",
        )
        assert resp.status_code in (400, 422), resp.text

    def test_reveal_secret_rejects_oversized_name(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/connections/{_OVER_128_CHARS}/secrets/api_key",
        )
        assert resp.status_code in (400, 422), resp.text

    def test_reveal_secret_rejects_oversized_field(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/connections/github-prod/secrets/{_OVER_128_CHARS}",
        )
        assert resp.status_code in (400, 422), resp.text


@pytest.mark.integration
class TestMcpCatalogPathParams:
    """``mcp_catalog.py`` -- typed entry_id on get + uninstall."""

    def test_get_entry_rejects_oversized_id(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/integrations/mcp/catalog/{_OVER_128_CHARS}",
        )
        assert resp.status_code in (400, 422), resp.text

    def test_uninstall_entry_rejects_oversized_id(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.delete(
            f"/api/v1/integrations/mcp/catalog/install/{_OVER_128_CHARS}",
        )
        assert resp.status_code in (400, 422), resp.text


@pytest.mark.integration
class TestOAuthPathParams:
    """``oauth.py`` -- typed connection_name on token_status."""

    def test_token_status_rejects_oversized_connection_name(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/oauth/status/{_OVER_128_CHARS}",
        )
        assert resp.status_code in (400, 422), resp.text


@pytest.mark.integration
class TestWebhooksPathParams:
    """``webhooks.py`` -- typed connection_name on list_activity.

    The POST ``/{connection_name}/{event_type}`` receive endpoint is
    covered by ``test_path_param_aliases`` below.  Driving it through
    ``TestClient`` would require fully wiring the per-op rate-limit
    guard (store + config + trusted-proxy state) and the message bus
    -- the alias contract is the same constraint either way, so the
    boundary test focuses on the type aliases directly.
    """

    def test_list_activity_rejects_oversized_connection_name(
        self, path_param_client: TestClient[Any]
    ) -> None:
        resp = path_param_client.get(
            f"/api/v1/webhooks/{_OVER_128_CHARS}/activity",
        )
        assert resp.status_code in (400, 422), resp.text


@pytest.mark.integration
class TestPathParamAliases:
    """Direct contract on the alias metadata in ``synthorg.api.path_params``.

    The HTTP-layer tests above prove that Litestar honours the alias
    constraints; this class pins the alias bounds themselves so a
    silent edit to ``path_params.py`` (relaxing ``max_length``,
    dropping ``min_length``) fails here too.  Audit-22 explicitly
    flagged ``connection_name`` (128) and ``event_type`` (64) as the
    canonical caps for the integration controllers.
    """

    @pytest.mark.parametrize(
        ("alias_name", "expected_max", "expected_min"),
        [
            ("PathId", 128, 1),
            ("PathName", 128, 1),
            ("PathNamespace", 64, 1),
            ("PathKey", 128, 1),
            ("PathField", 128, 1),
            ("PathEventType", 64, 1),
        ],
    )
    def test_alias_carries_expected_bounds(
        self,
        alias_name: str,
        expected_max: int,
        expected_min: int,
    ) -> None:
        """Each alias's ``Parameter`` metadata declares the audit-22 bounds."""
        from typing import get_args

        from litestar.params import ParameterKwarg

        from synthorg.api import path_params as pp

        alias = getattr(pp, alias_name)
        # Annotated aliases expose ``(underlying_type, *metadata)`` via
        # ``typing.get_args``.  Aliases that nest ``NotBlankStr`` (which
        # is itself ``Annotated[str, StringConstraints(min_length=1),
        # AfterValidator(...)]``) flatten through ``get_args`` so the
        # metadata tuple carries StringConstraints + AfterValidator
        # entries before the ``Parameter()``.  Filter on the concrete
        # ``ParameterKwarg`` (Litestar's runtime type for
        # ``Parameter()``) so the assertion picks the audit bound, not
        # the StringConstraints whose ``max_length`` is ``None``.
        meta = get_args(alias)
        assert meta[0] is str, f"{alias_name} should annotate ``str``"
        params = [m for m in meta[1:] if isinstance(m, ParameterKwarg)]
        assert params, f"{alias_name} is missing a Parameter() metadata entry"
        param = params[0]
        assert param.max_length == expected_max, (
            f"{alias_name}.max_length = {param.max_length}; expected {expected_max}"
        )
        assert param.min_length == expected_min, (
            f"{alias_name}.min_length = {param.min_length}; expected {expected_min}"
        )
        # ``PathField`` and ``PathEventType`` wrap ``NotBlankStr`` to
        # reject whitespace-only path segments in addition to the
        # length bound.  Drive a behavioural assertion so a regression
        # that swaps ``_check_not_whitespace`` for an unrelated
        # ``AfterValidator`` (or drops the alias back to bare ``str``)
        # fails here -- a metadata-shape check alone is spoofable.
        if alias_name in ("PathField", "PathEventType"):
            from pydantic import TypeAdapter, ValidationError

            with pytest.raises(ValidationError):
                TypeAdapter(alias).validate_python("   ")
