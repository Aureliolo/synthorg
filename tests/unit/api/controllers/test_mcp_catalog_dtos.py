"""DTO validation tests for the MCP catalog controller."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.datastructures import State
from pydantic import ValidationError

from synthorg.api.controllers.mcp_catalog import (
    InstallEntryRequest,
    InstallEntryResponse,
    MCPCatalogController,
)
from synthorg.core.domain_errors import ValidationError as DomainValidationError


@pytest.mark.unit
class TestInstallEntryRequest:
    """Pydantic validation boundary for POST /catalog/install."""

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="missing_catalog_entry_id"),
            pytest.param({"catalog_entry_id": ""}, id="blank_catalog_entry_id"),
            pytest.param({"catalog_entry_id": "   "}, id="whitespace_catalog_entry_id"),
            pytest.param(
                {"catalog_entry_id": "filesystem-mcp", "connection_name": 42},
                id="non_string_connection_name",
            ),
            pytest.param(
                {"catalog_entry_id": "filesystem-mcp", "connection_name": ""},
                id="blank_connection_name",
            ),
            pytest.param(
                {"catalog_entry_id": "filesystem-mcp", "connection_name": "   "},
                id="whitespace_connection_name",
            ),
            pytest.param(
                {"catalog_entry_id": "filesystem-mcp", "unknown_field": "x"},
                id="extra_field_forbidden",
            ),
        ],
    )
    def test_rejects_invalid_payload(self, payload: dict[str, Any]) -> None:
        """DTO rejects invalid payloads at the framework boundary."""
        with pytest.raises(ValidationError):
            InstallEntryRequest(**payload)

    def test_accepts_minimal_valid_payload(self) -> None:
        req = InstallEntryRequest(catalog_entry_id="filesystem-mcp")
        assert req.catalog_entry_id == "filesystem-mcp"
        assert req.connection_name is None

    def test_accepts_full_valid_payload(self) -> None:
        req = InstallEntryRequest(
            catalog_entry_id="github-mcp",
            connection_name="my-github",
        )
        assert req.catalog_entry_id == "github-mcp"
        assert req.connection_name == "my-github"

    def test_is_frozen(self) -> None:
        req = InstallEntryRequest(catalog_entry_id="filesystem-mcp")
        with pytest.raises(ValidationError):
            req.catalog_entry_id = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestInstallEntryValidateFirst:
    """Pre-validate ``connection_name`` before INSERT.

    Without pre-validation an unknown ``connection_name`` reaches the
    persistence layer and surfaces as a 500 from a
    ``psycopg.errors.ForeignKeyViolation``. The fix is two-pronged:
    (a) the controller pre-validates against
    ``connection_catalog.get(...)`` and raises ``ValidationError`` (-> 400)
    before calling the install service, and (b) ``EXCEPTION_HANDLERS``
    registers an ``IntegrityError -> 400`` backstop to catch the racy
    "connection deleted between validate and INSERT" path.

    The DTO test class above exercises the request DTO; this class
    drives the controller method directly with mocks so we exercise
    the validate-first branch without spinning up the full app.
    """

    async def test_unknown_connection_name_raises_validation_error(self) -> None:
        """Pre-validation rejects unknown ``connection_name`` -> ValidationError."""
        # The Litestar @post decorator wraps the method as an
        # HTTPRouteHandler; ``.fn`` is the underlying coroutine that
        # we can invoke directly with mocks for a unit-level test.
        install_entry = MCPCatalogController.install_entry.fn

        connection_catalog = MagicMock()
        connection_catalog.get = AsyncMock(return_value=None)

        app_state = MagicMock()
        app_state.mcp_catalog_service = MagicMock()
        app_state.mcp_installations_repo = MagicMock()
        app_state.has_connection_catalog = True
        app_state.connection_catalog = connection_catalog

        state = State({"app_state": app_state})
        data = InstallEntryRequest(
            catalog_entry_id="filesystem-mcp",
            connection_name="does-not-exist",
        )
        with pytest.raises(DomainValidationError, match="unknown connection"):
            await install_entry(self=None, state=state, data=data)
        # Ensure we never reached the install service.
        app_state.mcp_catalog_service.install.assert_not_called()
        connection_catalog.get.assert_awaited_once_with("does-not-exist")

    async def test_missing_connection_catalog_with_connection_name_raises(
        self,
    ) -> None:
        """Without ``connection_catalog`` wired the pre-validation cannot
        run -- raise ValidationError instead of letting an unbound name
        slip through to the persistence layer."""
        install_entry = MCPCatalogController.install_entry.fn

        app_state = MagicMock()
        app_state.mcp_catalog_service = MagicMock()
        app_state.mcp_installations_repo = MagicMock()
        app_state.has_connection_catalog = False

        state = State({"app_state": app_state})
        data = InstallEntryRequest(
            catalog_entry_id="filesystem-mcp",
            connection_name="anything",
        )
        with pytest.raises(
            DomainValidationError,
            match="Integrations subsystem is not configured",
        ):
            await install_entry(self=None, state=state, data=data)
        app_state.mcp_catalog_service.install.assert_not_called()


@pytest.mark.unit
class TestInstallEntryResponse:
    """Pydantic validation boundary for the install response DTO."""

    def test_accepts_valid_payload(self) -> None:
        resp = InstallEntryResponse(
            status="installed",
            server_name="Filesystem",
            catalog_entry_id="filesystem-mcp",
            tool_count=3,
        )
        assert resp.status == "installed"
        assert resp.tool_count == 3

    def test_rejects_non_installed_status(self) -> None:
        with pytest.raises(ValidationError):
            InstallEntryResponse(
                status="pending",  # type: ignore[arg-type]
                server_name="Filesystem",
                catalog_entry_id="filesystem-mcp",
                tool_count=3,
            )

    def test_rejects_negative_tool_count(self) -> None:
        with pytest.raises(ValidationError):
            InstallEntryResponse(
                status="installed",
                server_name="Filesystem",
                catalog_entry_id="filesystem-mcp",
                tool_count=-1,
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            InstallEntryResponse(
                status="installed",
                server_name="Filesystem",
                catalog_entry_id="filesystem-mcp",
                tool_count=3,
                extra_field="x",  # type: ignore[call-arg]
            )
