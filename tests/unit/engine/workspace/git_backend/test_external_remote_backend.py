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
from synthorg.engine.workspace.git_backend.protocol import (
    ResolvedSource,
    SourceKind,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from tests._shared import FakeClock, mock_of

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
        name=NotBlankStr("forge-main"),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr(base_url) if base_url else None,
    )


def _backend(catalog: ConnectionCatalog) -> ExternalRemoteGitBackend:
    return ExternalRemoteGitBackend(
        connection_name="forge-main",
        connection_catalog=catalog,
        cmd_timeout=30.0,
        clock=FakeClock(),
    )


def _hardened_backend(catalog: ConnectionCatalog) -> ExternalRemoteGitBackend:
    """Backend with zero-delay retry so tests do not sleep."""
    return ExternalRemoteGitBackend(
        connection_name="forge-main",
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

    ``push_results`` / ``fetch_results`` are queues of ``(rc, stderr)``
    consumed in order by successive ``push`` / ``fetch`` calls; an empty
    ``fetch`` queue succeeds. ``rev-parse`` always returns a SHA.
    """

    def __init__(
        self,
        push_results: Sequence[tuple[int, str]],
        fetch_results: Sequence[tuple[int, str]] | None = None,
    ) -> None:
        self._push = list(push_results)
        self._fetch = list(fetch_results or [])
        self.push_count = 0
        self.fetch_count = 0

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
        if sub == "fetch":
            self.fetch_count += 1
            if self._fetch:
                rc, stderr = self._fetch.pop(0)
                return rc, "", stderr
            return 0, "", ""
        if sub == "rev-parse":
            return 0, "deadbeefcafe", ""
        return 0, "", ""


def _catalog_forge() -> Any:
    catalog = mock_of[ConnectionCatalog]()
    catalog.get.return_value = _connection("https://example-provider.invalid/acme")
    catalog.get_credentials.return_value = {"token": "secret-token"}
    return catalog


def _fake_forge(*, exists: bool) -> Any:
    forge = mock_of[ForgeApiClient]()
    forge.repo_exists.return_value = exists
    forge.create_repo.return_value = ForgeRepo(
        full_name=NotBlankStr("acme/p1"),
        default_branch=NotBlankStr("main"),
        private=True,
        clone_url=NotBlankStr("https://example-provider.invalid/acme/p1.git"),
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
        backend = _hardened_backend(_catalog_forge())

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
        backend = _hardened_backend(_catalog_forge())

        with pytest.raises(GitBackendRateLimitError):
            await backend.push(
                project_id=NotBlankStr("p1"),
                repo_root=tmp_path,
                branch=NotBlankStr("main"),
                base_branch=NotBlankStr("main"),
            )
        assert fake.push_count == 3

    @pytest.mark.parametrize(
        "stderr",
        [
            # Regression for the substring-match false-positive: a localhost
            # URL with a random port containing "429" as a substring
            # ("42919", "1429", "4290", ...) must NOT classify as a
            # rate-limit. The integration test test_lazy_create_on_missing_remote
            # tripped this because Python's http.server picks ephemeral
            # ports in the 32k-60k range, occasionally hitting "429"
            # substrings, which then misclassified a clean "repo not
            # found" as a rate-limit and exhausted the retry budget.
            "fatal: repository 'https://localhost:42919/acme/proj-new.git/' not found",
            "fatal: repository 'https://localhost:14290/acme/proj-new.git/' not found",
            "fatal: repository 'https://localhost:34291/acme/proj-new.git/' not found",
        ],
    )
    async def test_port_containing_429_substring_not_rate_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        stderr: str,
    ) -> None:
        # Push fails with a "repo not found" stderr whose URL embeds a
        # port that contains "429" as a digit-substring. The classifier
        # must reach the remote-missing branch (forge.exists=False) and
        # raise GitBackendRemoteMissingError-driven lazy-create, NOT
        # raise GitBackendRateLimitError. The second push attempt
        # (after lazy create) succeeds.
        fake = _FakeGit([(1, stderr), (0, "")])
        _patch_git(monkeypatch, fake)
        forge = _fake_forge(exists=False)
        _patch_forge(monkeypatch, forge)
        backend = _hardened_backend(_catalog_forge())

        result = await backend.push(
            project_id=NotBlankStr("proj-new"),
            repo_root=tmp_path,
            branch=NotBlankStr("main"),
            base_branch=NotBlankStr("main"),
        )
        forge.create_repo.assert_awaited_once()
        assert fake.push_count == 2
        assert str(result.head_sha)

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
        backend = _hardened_backend(_catalog_forge())

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
            connection_name="forge-main",
            connection_catalog=_catalog_forge(),
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
        backend = _hardened_backend(_catalog_forge())

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

    async def test_fetch_auth_failure_is_not_retried(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # A fetch whose stderr carries an auth marker is classified as a
        # non-retryable GitBackendForgeAuthError, so the handler makes a
        # single attempt rather than burning the retry budget.
        fake = _FakeGit([], fetch_results=[(1, "fatal: Authentication failed")])
        _patch_git(monkeypatch, fake)
        backend = _hardened_backend(_catalog_forge())

        with pytest.raises(GitBackendForgeAuthError):
            await backend.fetch(
                project_id=NotBlankStr("p1"),
                repo_root=tmp_path,
                branch=NotBlankStr("main"),
            )
        assert fake.fetch_count == 1

    async def test_fetch_transient_failure_retries_then_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake = _FakeGit(
            [],
            fetch_results=[(1, "fatal: unable to access: 500"), (0, "")],
        )
        _patch_git(monkeypatch, fake)
        backend = _hardened_backend(_catalog_forge())

        result = await backend.fetch(
            project_id=NotBlankStr("p1"),
            repo_root=tmp_path,
            branch=NotBlankStr("main"),
        )
        assert fake.fetch_count == 2
        assert result.updated_refs == (NotBlankStr("main"),)


class TestExternalRemoteSeed:
    async def test_seed_imports_then_pushes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Empty workspace (ls-files reports nothing tracked or untracked),
        # source fetched, branch reset, then the imported head pushed to an
        # existing forge repo.
        fake = _FakeGit([(0, "")])
        _patch_git(monkeypatch, fake)
        _patch_forge(monkeypatch, _fake_forge(exists=True))
        backend = _hardened_backend(_catalog_forge())

        result = await backend.seed(
            project_id=NotBlankStr("p1"),
            repo_root=tmp_path,
            source=ResolvedSource(
                fetch_url=NotBlankStr(
                    "https://example-provider.invalid/acme/legacy.git"
                ),
                source_kind=SourceKind.REMOTE,
            ),
            default_branch=NotBlankStr("main"),
        )

        assert result.head_sha == "deadbeefcafe"
        assert result.source_kind is SourceKind.REMOTE
        assert fake.fetch_count == 1
        assert fake.push_count == 1
