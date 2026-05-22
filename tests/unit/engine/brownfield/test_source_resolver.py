"""Unit tests for ``BrownfieldSourceResolver``."""

from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield import source_resolver as resolver_mod
from synthorg.engine.brownfield.errors import BrownfieldSourceUnavailableError
from synthorg.engine.brownfield.source_resolver import BrownfieldSourceResolver
from synthorg.engine.workspace.git_backend.protocol import SourceKind
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.tools.git_url_validator import DnsValidationOk
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestLocalSource:
    async def test_local_directory_resolves(self, tmp_path: Path) -> None:
        result = await BrownfieldSourceResolver().resolve(NotBlankStr(str(tmp_path)))
        assert result.source_kind is SourceKind.LOCAL_PATH
        assert result.fetch_url == str(tmp_path)

    async def test_file_url_resolves(self, tmp_path: Path) -> None:
        result = await BrownfieldSourceResolver().resolve(
            NotBlankStr(f"file://{tmp_path}")
        )
        assert result.source_kind is SourceKind.LOCAL_PATH

    async def test_missing_local_path_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(BrownfieldSourceUnavailableError):
            await BrownfieldSourceResolver().resolve(NotBlankStr(str(missing)))


class TestRemoteSource:
    async def test_disallowed_scheme_rejected(self) -> None:
        with pytest.raises(BrownfieldSourceUnavailableError):
            await BrownfieldSourceResolver().resolve(
                NotBlankStr("http://insecure.example.com/acme/legacy.git")
            )

    async def test_ssrf_block_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _blocked(_url: str, _policy: object) -> str:
            return "SSRF blocked: private IP"

        monkeypatch.setattr(resolver_mod, "validate_clone_url_host", _blocked)

        with pytest.raises(BrownfieldSourceUnavailableError, match="SSRF"):
            await BrownfieldSourceResolver().resolve(
                NotBlankStr("https://internal.example.com/acme/legacy.git")
            )

    async def test_matching_connection_injects_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _ok(_url: str, _policy: object) -> DnsValidationOk:
            return DnsValidationOk(
                hostname=NotBlankStr("git.example.com"),
                port=443,
                resolved_ips=(),
                is_https=True,
            )

        monkeypatch.setattr(resolver_mod, "validate_clone_url_host", _ok)

        catalog = mock_of[ConnectionCatalog]()
        catalog.list_all.return_value = (
            Connection(
                name=NotBlankStr("forge-main"),
                connection_type=ConnectionType.GITHUB,
                auth_method=AuthMethod.API_KEY,
                base_url=NotBlankStr("https://git.example.com/acme"),
            ),
        )
        catalog.get_credentials.return_value = {"token": "secret-token"}

        resolver = BrownfieldSourceResolver(connection_catalog=catalog)
        result = await resolver.resolve(
            NotBlankStr("https://git.example.com/acme/legacy.git")
        )

        assert result.source_kind is SourceKind.REMOTE
        assert "x-access-token:secret-token@git.example.com" in result.fetch_url

    async def test_no_matching_connection_stays_anonymous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _ok(_url: str, _policy: object) -> DnsValidationOk:
            return DnsValidationOk(
                hostname=NotBlankStr("git.example.com"),
                port=443,
                resolved_ips=(),
                is_https=True,
            )

        monkeypatch.setattr(resolver_mod, "validate_clone_url_host", _ok)

        catalog = mock_of[ConnectionCatalog]()
        catalog.list_all.return_value = ()

        resolver = BrownfieldSourceResolver(connection_catalog=catalog)
        result = await resolver.resolve(
            NotBlankStr("https://git.example.com/acme/legacy.git")
        )

        assert result.fetch_url == "https://git.example.com/acme/legacy.git"
