"""Unit tests for the per-forge REST clients + factory.

Exercises repo existence / creation, the user-vs-org endpoint split,
rate-limit + auth error mapping, and the API-base-URL derivation,
against ``respx``-mocked HTTP (no live forge).
"""

from typing import cast

import httpx
import pytest
import respx

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendRateLimitError,
)
from synthorg.engine.workspace.git_backend.forge_api import build_forge_api_client
from synthorg.engine.workspace.git_backend.forge_api._base import BaseForgeClient
from synthorg.engine.workspace.git_backend.forge_api.gitea import GiteaForgeClient
from synthorg.engine.workspace.git_backend.forge_api.github import GitHubForgeClient
from synthorg.engine.workspace.git_backend.forge_api.gitlab import GitLabForgeClient
from synthorg.integrations.connections.models import ConnectionType

pytestmark = pytest.mark.unit

_OWNER = NotBlankStr("acme")
_REPO = NotBlankStr("proj-1")


def _github() -> GitHubForgeClient:
    return GitHubForgeClient(
        api_base_url="https://api.github.com",
        token="t0ken",
        timeout=5.0,
    )


class TestGitHubForgeClient:
    @respx.mock
    async def test_repo_exists_true(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(200, json={"full_name": "acme/proj-1"}),
        )
        async with _github() as client:
            assert await client.repo_exists(owner=_OWNER, repo=_REPO) is True

    @respx.mock
    async def test_repo_exists_false_on_404(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(404),
        )
        async with _github() as client:
            assert await client.repo_exists(owner=_OWNER, repo=_REPO) is False

    @respx.mock
    async def test_create_repo_under_org(self) -> None:
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "someone-else"}),
        )
        org_route = respx.post("https://api.github.com/orgs/acme/repos").mock(
            return_value=httpx.Response(
                201,
                json={
                    "full_name": "acme/proj-1",
                    "default_branch": "main",
                    "private": True,
                    "clone_url": "https://github.com/acme/proj-1.git",
                },
            ),
        )
        async with _github() as client:
            repo = await client.create_repo(owner=_OWNER, repo=_REPO, private=True)
        assert org_route.called
        assert str(repo.full_name) == "acme/proj-1"
        assert repo.private is True

    @respx.mock
    async def test_create_repo_under_user(self) -> None:
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "acme"}),
        )
        user_route = respx.post("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                201,
                json={
                    "full_name": "acme/proj-1",
                    "default_branch": "trunk",
                    "private": False,
                    "clone_url": "https://github.com/acme/proj-1.git",
                },
            ),
        )
        async with _github() as client:
            repo = await client.create_repo(owner=_OWNER, repo=_REPO, private=False)
        assert user_route.called
        assert str(repo.default_branch) == "trunk"

    @respx.mock
    async def test_rate_limit_429_maps_to_typed_error(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendRateLimitError) as exc_info:
                await client.repo_exists(owner=_OWNER, repo=_REPO)
        assert exc_info.value.retry_after == 12.0

    @respx.mock
    async def test_primary_rate_limit_403_remaining_zero(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendRateLimitError):
                await client.repo_exists(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_auth_403_maps_to_auth_error(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(403, json={"message": "Bad credentials"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeAuthError):
                await client.repo_exists(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_auth_401_maps_to_auth_error(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(401, json={"message": "Requires auth"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeAuthError):
                await client.repo_exists(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_403_without_ratelimit_header_maps_to_auth_error(self) -> None:
        # A 403 that is NOT a primary rate-limit (no X-RateLimit-Remaining: 0)
        # is an authorization failure, not a retryable rate-limit.
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            return_value=httpx.Response(403, json={"message": "Forbidden"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeAuthError):
                await client.repo_exists(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_transport_error_maps_to_forge_api_error(self) -> None:
        respx.get("https://api.github.com/repos/acme/proj-1").mock(
            side_effect=httpx.ConnectError("boom"),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeApiError):
                await client.repo_exists(owner=_OWNER, repo=_REPO)


class TestGitLabForgeClient:
    @respx.mock
    async def test_repo_exists_url_encoded_path(self) -> None:
        respx.get("https://gitlab.example.com/api/v4/projects/acme%2Fproj-1").mock(
            return_value=httpx.Response(200, json={"id": 1}),
        )
        client = GitLabForgeClient(
            api_base_url="https://gitlab.example.com/api/v4",
            token="t",
            timeout=5.0,
        )
        async with client:
            assert await client.repo_exists(owner=_OWNER, repo=_REPO) is True

    @respx.mock
    async def test_create_repo_resolves_group_namespace(self) -> None:
        respx.get("https://gitlab.example.com/api/v4/user").mock(
            return_value=httpx.Response(200, json={"username": "someone"}),
        )
        respx.get("https://gitlab.example.com/api/v4/namespaces/acme").mock(
            return_value=httpx.Response(200, json={"id": 42}),
        )
        create = respx.post("https://gitlab.example.com/api/v4/projects").mock(
            return_value=httpx.Response(
                201,
                json={
                    "path_with_namespace": "acme/proj-1",
                    "default_branch": "main",
                    "visibility": "private",
                    "http_url_to_repo": "https://gitlab.example.com/acme/proj-1.git",
                },
            ),
        )
        client = GitLabForgeClient(
            api_base_url="https://gitlab.example.com/api/v4",
            token="t",
            timeout=5.0,
        )
        async with client:
            repo = await client.create_repo(owner=_OWNER, repo=_REPO)
        assert create.called
        assert b'"namespace_id":42' in create.calls.last.request.content
        assert str(repo.full_name) == "acme/proj-1"


class TestGiteaForgeClient:
    @respx.mock
    async def test_create_repo_under_user(self) -> None:
        respx.get("https://gitea.example.com/api/v1/user").mock(
            return_value=httpx.Response(200, json={"login": "acme"}),
        )
        create = respx.post("https://gitea.example.com/api/v1/user/repos").mock(
            return_value=httpx.Response(
                201,
                json={
                    "full_name": "acme/proj-1",
                    "default_branch": "main",
                    "private": True,
                    "clone_url": "https://gitea.example.com/acme/proj-1.git",
                },
            ),
        )
        client = GiteaForgeClient(
            api_base_url="https://gitea.example.com/api/v1",
            token="t",
            timeout=5.0,
        )
        async with client:
            repo = await client.create_repo(owner=_OWNER, repo=_REPO)
        assert create.called
        assert str(repo.full_name) == "acme/proj-1"


class TestFactoryApiBaseDerivation:
    @pytest.mark.parametrize(
        ("ctype", "base_url", "expected_api"),
        [
            (
                ConnectionType.GITHUB,
                "https://github.com/acme",
                "https://api.github.com/",
            ),
            (
                ConnectionType.GITHUB,
                "https://ghe.corp.example/acme",
                "https://ghe.corp.example/api/v3/",
            ),
            (
                ConnectionType.GITLAB,
                "https://gitlab.com/acme",
                "https://gitlab.com/api/v4/",
            ),
            (
                ConnectionType.GITEA,
                "https://gitea.example.com/acme",
                "https://gitea.example.com/api/v1/",
            ),
            (
                ConnectionType.FORGEJO,
                "https://code.example.com/acme",
                "https://code.example.com/api/v1/",
            ),
        ],
        ids=[
            "github-public",
            "github-enterprise",
            "gitlab-com",
            "gitea-self-hosted",
            "forgejo-self-hosted",
        ],
    )
    def test_api_base_derivation(
        self,
        ctype: ConnectionType,
        base_url: str,
        expected_api: str,
    ) -> None:
        client = build_forge_api_client(
            connection_type=ctype,
            base_url=base_url,
            token="t",
            timeout=5.0,
        )
        assert cast("BaseForgeClient", client)._api_base_url == expected_api
