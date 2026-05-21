"""Unit tests for ``ExternalRemoteGitBackend``.

Covers the catalog-mocked resolution + fail-fast surface, plus the
hardening behaviour: transient-failure retry, lazy forge-repo
creation on a missing remote, and rate-limit / auth classification.
The git subprocess and forge REST client are mocked (no live forge).
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendForgeAuthError,
    GitBackendPushError,
    GitBackendRateLimitError,
)
from synthorg.engine.workspace.git_backend import ExternalRemoteGitBackend
from synthorg.engine.workspace.git_backend.config import GitBackendResilienceConfig
from synthorg.engine.workspace.git_backend.forge_api import ForgeApiClient, ForgeRepo
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)

pytestmark = pytest.mark.unit

_FORGE_API = (
    "synthorg.engine.workspace.git_backend.external_remote.build_forge_api_client"
)
_GIT_SUBPROC_EXTERNAL = (
    "synthorg.engine.workspace.git_backend.external_remote.run_git_subprocess"
)
_GIT_SUBPROC_OPS = "synthorg.engine.workspace.git_backend._git_ops.run_git_subprocess"


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


def _hardened_backend(catalog: ConnectionCatalog) -> ExternalRemoteGitBackend:
    """Backend with zero-delay retry so tests do not sleep."""
    return ExternalRemoteGitBackend(
        connection_name="github-main",
        connection_catalog=catalog,
        cmd_timeout=30.0,
        resilience=GitBackendResilienceConfig(
            max_attempts=3,
            base_delay_seconds=0.0,
            cap_delay_seconds=0.1,
            jitter=False,
        ),
        clock=FakeClock(),
    )


class _FakeGit:
    """Routes mocked ``run_git_subprocess`` calls by git subcommand.

    ``push_results`` is a queue of ``(rc, stderr)`` consumed in order
    by successive ``push`` calls; ``rev-parse`` always returns a SHA.
    """

    def __init__(self, push_results: Sequence[tuple[int, str]]) -> None:
        self._push = list(push_results)
        self.push_count = 0

    async def __call__(
        self,
        _repo_root: Path,
        *args: str,
        cmd_timeout: float,
        log_event: str,
    ) -> tuple[int, str, str]:
        sub = args[0] if args else ""
        if sub == "push":
            self.push_count += 1
            rc, stderr = self._push.pop(0)
            return rc, "", stderr
        if sub == "rev-parse":
            return 0, "deadbeefcafe", ""
        return 0, "", ""


def _catalog_github() -> Any:
    catalog = mock_of[ConnectionCatalog]()
    catalog.get.return_value = _connection("https://github.com/acme")
    catalog.get_credentials.return_value = {"token": "secret-token"}
    return catalog


def _fake_forge(*, exists: bool) -> Any:
    forge = mock_of[ForgeApiClient]()
    forge.repo_exists.return_value = exists
    forge.create_repo.return_value = ForgeRepo(
        full_name=NotBlankStr("acme/p1"),
        default_branch=NotBlankStr("main"),
        private=True,
        clone_url=NotBlankStr("https://github.com/acme/p1.git"),
    )
    return forge


def _patch_git(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeGit,
) -> None:
    monkeypatch.setattr(_GIT_SUBPROC_EXTERNAL, fake)
    monkeypatch.setattr(_GIT_SUBPROC_OPS, fake)


def _patch_forge(
    monkeypatch: pytest.MonkeyPatch,
    forge: ForgeApiClient,
) -> None:
    factory: Callable[..., ForgeApiClient] = lambda **_kw: forge  # noqa: E731
    monkeypatch.setattr(_FORGE_API, factory)


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


class TestExternalRemotePushHardening:
    async def test_transient_push_retries_then_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # First push fails transiently, second succeeds. The forge repo
        # exists, so the failure is classified as a retryable push error.
        fake = _FakeGit([(1, "fatal: unable to access: 500"), (0, "")])
        _patch_git(monkeypatch, fake)
        _patch_forge(monkeypatch, _fake_forge(exists=True))
        backend = _hardened_backend(_catalog_github())

        result = await backend.push(
            project_id=NotBlankStr("p1"),
            repo_root=tmp_path,
            branch=NotBlankStr("main"),
            base_branch=NotBlankStr("main"),
        )
        assert fake.push_count == 2
        assert str(result.head_sha) == "deadbeefcafe"

    async def test_rate_limit_marker_classified_and_retried_then_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # A push stderr carrying a rate-limit marker is classified as a
        # retryable GitBackendRateLimitError; when every attempt is rate
        # limited the handler exhausts and re-raises that typed error.
        fake = _FakeGit([(1, "429: too many requests")] * 3)
        _patch_git(monkeypatch, fake)
        _patch_forge(monkeypatch, _fake_forge(exists=True))
        backend = _hardened_backend(_catalog_github())

        with pytest.raises(GitBackendRateLimitError):
            await backend.push(
                project_id=NotBlankStr("p1"),
                repo_root=tmp_path,
                branch=NotBlankStr("main"),
                base_branch=NotBlankStr("main"),
            )
        assert fake.push_count == 3

    async def test_missing_remote_creates_repo_then_retries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # First push fails; forge says repo missing -> create_repo, then
        # the push is retried once and succeeds.
        fake = _FakeGit([(1, "remote: Repository not found"), (0, "")])
        _patch_git(monkeypatch, fake)
        forge = _fake_forge(exists=False)
        _patch_forge(monkeypatch, forge)
        backend = _hardened_backend(_catalog_github())

        await backend.push(
            project_id=NotBlankStr("p1"),
            repo_root=tmp_path,
            branch=NotBlankStr("main"),
            base_branch=NotBlankStr("main"),
        )
        forge.create_repo.assert_awaited_once()
        assert fake.push_count == 2

    async def test_missing_remote_not_created_when_provisioning_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake = _FakeGit([(1, "remote: Repository not found")])
        _patch_git(monkeypatch, fake)
        forge = _fake_forge(exists=False)
        _patch_forge(monkeypatch, forge)
        backend = ExternalRemoteGitBackend(
            connection_name="github-main",
            connection_catalog=_catalog_github(),
            cmd_timeout=30.0,
            resilience=GitBackendResilienceConfig(
                max_attempts=1,
                base_delay_seconds=0.0,
                cap_delay_seconds=0.1,
            ),
            forge_provisioning_enabled=False,
            clock=FakeClock(),
        )

        with pytest.raises(GitBackendPushError):
            await backend.push(
                project_id=NotBlankStr("p1"),
                repo_root=tmp_path,
                branch=NotBlankStr("main"),
                base_branch=NotBlankStr("main"),
            )
        forge.create_repo.assert_not_called()

    async def test_auth_failure_is_not_retried(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake = _FakeGit([(1, "fatal: Authentication failed for repo")])
        _patch_git(monkeypatch, fake)
        forge = _fake_forge(exists=True)
        _patch_forge(monkeypatch, forge)
        backend = _hardened_backend(_catalog_github())

        with pytest.raises(GitBackendForgeAuthError):
            await backend.push(
                project_id=NotBlankStr("p1"),
                repo_root=tmp_path,
                branch=NotBlankStr("main"),
                base_branch=NotBlankStr("main"),
            )
        # Auth is non-retryable: exactly one attempt, no existence probe.
        assert fake.push_count == 1
        forge.repo_exists.assert_not_called()
