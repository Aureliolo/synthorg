"""Unit tests for ``ExternalRemoteGitBackend`` (catalog-mocked surface).

Deep behaviour (real clone/push, token refresh) is the tracked
hardening follow-up; these pin the resolution + fail-fast surface.
"""

from pathlib import Path

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend import ExternalRemoteGitBackend
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)

pytestmark = pytest.mark.unit


def _connection(base_url: str | None) -> Connection:
    return Connection(
        name=NotBlankStr("github-main"),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr(base_url) if base_url else None,
    )


def _backend(catalog: ConnectionCatalog) -> ExternalRemoteGitBackend:
    return ExternalRemoteGitBackend(
        connection_name="github-main",
        connection_catalog=catalog,
        cmd_timeout=30.0,
        clock=FakeClock(),
    )


class TestExternalRemoteGitBackend:
    async def test_unregistered_connection_fails_fast(self, tmp_path: Path) -> None:
        catalog = mock_of[ConnectionCatalog]()
        catalog.get.return_value = None
        backend = _backend(catalog)

        with pytest.raises(GitBackendConfigError, match="not registered"):
            await backend.provision(
                project_id=NotBlankStr("p1"),
                workspace_path=tmp_path / "ws",
                default_branch=NotBlankStr("main"),
            )

    async def test_missing_token_fails_fast(self, tmp_path: Path) -> None:
        catalog = mock_of[ConnectionCatalog]()
        catalog.get.return_value = _connection("https://forge.example.com")
        catalog.get_credentials.return_value = {}
        backend = _backend(catalog)

        with pytest.raises(GitBackendConfigError, match="token"):
            await backend.provision(
                project_id=NotBlankStr("p1"),
                workspace_path=tmp_path / "ws",
                default_branch=NotBlankStr("main"),
            )

    async def test_non_https_base_url_rejected(self, tmp_path: Path) -> None:
        catalog = mock_of[ConnectionCatalog]()
        catalog.get.return_value = _connection("http://insecure.example.com")
        catalog.get_credentials.return_value = {"token": "t"}
        backend = _backend(catalog)

        with pytest.raises(GitBackendConfigError, match="https"):
            await backend.provision(
                project_id=NotBlankStr("p1"),
                workspace_path=tmp_path / "ws",
                default_branch=NotBlankStr("main"),
            )

    def test_backend_type(self) -> None:
        catalog = mock_of[ConnectionCatalog]()
        assert _backend(catalog).get_backend_type().value == "external_remote"
