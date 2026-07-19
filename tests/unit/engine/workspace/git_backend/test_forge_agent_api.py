"""Unit tests for the agent-operations forge clients + factory.

Exercises the read surface (repo/file/dir/issue/PR), the write surface
(issue/PR/comment/review/merge), CI reads, and the GitHub-vs-Gitea
divergences (review event vocabulary, merge payload, label resolution,
unsupported drafts + CI) against ``respx``-mocked HTTP (no live forge).
"""

import base64

import httpx
import pytest
import respx

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendRateLimitError,
)
from synthorg.engine.workspace.git_backend.forge_api import (
    build_forge_agent_api_client,
    forge_agent_api_supported,
)
from synthorg.engine.workspace.git_backend.forge_api.gitea_agent import (
    ForgejoAgentForgeClient,
)
from synthorg.engine.workspace.git_backend.forge_api.github_agent import (
    GitHubAgentForgeClient,
)
from synthorg.integrations.connections.models import ConnectionType

pytestmark = pytest.mark.unit

_OWNER = NotBlankStr("acme")
_REPO = NotBlankStr("proj-1")
_GH = "https://api.github.com"
_FJ = "https://code.example.com/api/v1"


def _github() -> GitHubAgentForgeClient:
    return GitHubAgentForgeClient(api_base_url=_GH, token="t0ken", timeout=5.0)


def _forgejo() -> ForgejoAgentForgeClient:
    return ForgejoAgentForgeClient(api_base_url=_FJ, token="t0ken", timeout=5.0)


class TestGitHubReadSurface:
    @respx.mock
    async def test_get_repo(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "full_name": "acme/proj-1",
                    "default_branch": "main",
                    "private": True,
                    "clone_url": "https://github.com/acme/proj-1.git",
                },
            ),
        )
        async with _github() as client:
            repo = await client.get_repo(owner=_OWNER, repo=_REPO)
        assert str(repo.full_name) == "acme/proj-1"
        assert repo.private is True

    @respx.mock
    async def test_read_file_decodes_base64(self) -> None:
        encoded = base64.b64encode(b"print('hi')\n").decode()
        respx.get(f"{_GH}/repos/acme/proj-1/contents/src/app.py").mock(
            return_value=httpx.Response(
                200,
                json={
                    "path": "src/app.py",
                    "encoding": "base64",
                    "content": encoded,
                    "sha": "abc",
                    "size": 12,
                },
            ),
        )
        async with _github() as client:
            result = await client.read_file(
                owner=_OWNER, repo=_REPO, path=NotBlankStr("src/app.py"), ref="main"
            )
        assert result.content == "print('hi')\n"
        assert result.ref == "main"
        assert str(result.path) == "src/app.py"

    @respx.mock
    async def test_read_file_rejects_non_file(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/contents/dir").mock(
            return_value=httpx.Response(200, json={"path": "dir", "encoding": ""}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeApiError):
                await client.read_file(
                    owner=_OWNER, repo=_REPO, path=NotBlankStr("dir")
                )

    @respx.mock
    async def test_list_dir(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/contents/src").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "app.py",
                        "path": "src/app.py",
                        "type": "file",
                        "size": 5,
                        "sha": "a",
                    },
                    {
                        "name": "pkg",
                        "path": "src/pkg",
                        "type": "dir",
                        "size": 0,
                        "sha": "b",
                    },
                ],
            ),
        )
        async with _github() as client:
            entries = await client.list_dir(owner=_OWNER, repo=_REPO, path="src")
        assert [e.kind for e in entries] == ["file", "dir"]
        assert str(entries[0].name) == "app.py"

    @respx.mock
    async def test_list_dir_rejects_single_object(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/contents/f.py").mock(
            return_value=httpx.Response(200, json={"name": "f.py", "type": "file"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeApiError):
                await client.list_dir(owner=_OWNER, repo=_REPO, path="f.py")

    @respx.mock
    async def test_list_issues_filters_pull_requests(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/issues").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "bug",
                        "state": "open",
                        "user": {"login": "a"},
                    },
                    {
                        "number": 2,
                        "title": "pr",
                        "state": "open",
                        "pull_request": {"url": "x"},
                    },
                ],
            ),
        )
        async with _github() as client:
            issues = await client.list_issues(owner=_OWNER, repo=_REPO, limit=10)
        assert [i.number for i in issues] == [1]

    @respx.mock
    async def test_get_pull_request_maps_branches(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/pulls/7").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "feat",
                    "state": "open",
                    "user": {"login": "dev"},
                    "head": {"ref": "feature/x"},
                    "base": {"ref": "main"},
                    "draft": True,
                },
            ),
        )
        async with _github() as client:
            pull = await client.get_pull_request(owner=_OWNER, repo=_REPO, number=7)
        assert pull.source_branch == "feature/x"
        assert pull.target_branch == "main"
        assert pull.draft is True


class TestGitHubWriteSurface:
    @respx.mock
    async def test_create_issue(self) -> None:
        route = respx.post(f"{_GH}/repos/acme/proj-1/issues").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 3,
                    "title": "new",
                    "state": "open",
                    "user": {"login": "a"},
                },
            ),
        )
        async with _github() as client:
            issue = await client.create_issue(
                owner=_OWNER, repo=_REPO, title=NotBlankStr("new"), labels=("bug",)
            )
        assert issue.number == 3
        assert b'"labels":["bug"]' in route.calls.last.request.content

    @respx.mock
    async def test_review_maps_approve_event(self) -> None:
        route = respx.post(f"{_GH}/repos/acme/proj-1/pulls/7/reviews").mock(
            return_value=httpx.Response(
                200, json={"id": 1, "state": "APPROVED", "user": {"login": "r"}}
            ),
        )
        async with _github() as client:
            review = await client.review_pull_request(
                owner=_OWNER, repo=_REPO, number=7, decision="approve"
            )
        assert review.state == "APPROVED"
        assert b'"event":"APPROVE"' in route.calls.last.request.content

    @respx.mock
    async def test_merge(self) -> None:
        route = respx.put(f"{_GH}/repos/acme/proj-1/pulls/7/merge").mock(
            return_value=httpx.Response(
                200,
                json={
                    "sha": "deadbeef",
                    "merged": True,
                    "message": "Pull Request merged",
                },
            ),
        )
        async with _github() as client:
            result = await client.merge_pull_request(
                owner=_OWNER, repo=_REPO, number=7, method="squash"
            )
        assert result.merged is True
        assert result.sha == "deadbeef"
        assert b'"merge_method":"squash"' in route.calls.last.request.content

    @respx.mock
    async def test_create_pull_request_maps_head_base(self) -> None:
        route = respx.post(f"{_GH}/repos/acme/proj-1/pulls").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 9,
                    "title": "t",
                    "state": "open",
                    "head": {"ref": "wip"},
                    "base": {"ref": "main"},
                },
            ),
        )
        async with _github() as client:
            pull = await client.create_pull_request(
                owner=_OWNER,
                repo=_REPO,
                title=NotBlankStr("t"),
                source_branch=NotBlankStr("wip"),
                target_branch=NotBlankStr("main"),
            )
        assert pull.number == 9
        body = route.calls.last.request.content
        assert b'"head":"wip"' in body
        assert b'"base":"main"' in body


class TestGitHubCi:
    @respx.mock
    async def test_list_ci_runs(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/actions/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 11,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "head_branch": "main",
                            "head_sha": "abc",
                            "html_url": "https://github.com/acme/proj-1/actions/runs/11",
                        }
                    ]
                },
            ),
        )
        async with _github() as client:
            runs = await client.list_ci_runs(owner=_OWNER, repo=_REPO, limit=5)
        assert runs[0].id == 11
        assert runs[0].conclusion == "success"

    @respx.mock
    async def test_auth_error_maps(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/actions/runs/11").mock(
            return_value=httpx.Response(401, json={"message": "Requires auth"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeAuthError):
                await client.get_ci_run(owner=_OWNER, repo=_REPO, run_id=11)

    @respx.mock
    async def test_rate_limit_429_maps_with_retry_after(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "13"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendRateLimitError) as exc_info:
                await client.get_repo(owner=_OWNER, repo=_REPO)
        assert exc_info.value.retry_after == 13.0

    @respx.mock
    async def test_primary_rate_limit_403_maps(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1").mock(
            return_value=httpx.Response(
                403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "limit"}
            ),
        )
        async with _github() as client:
            with pytest.raises(GitBackendRateLimitError):
                await client.get_repo(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_server_5xx_maps_to_api_error(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1").mock(
            return_value=httpx.Response(503, json={"message": "unavailable"}),
        )
        async with _github() as client:
            with pytest.raises(GitBackendForgeApiError):
                await client.get_repo(owner=_OWNER, repo=_REPO)


class TestForgejoDivergences:
    @respx.mock
    async def test_shared_read_surface_inherited(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/issues/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 1,
                    "title": "bug",
                    "state": "open",
                    "user": {"login": "a"},
                },
            ),
        )
        async with _forgejo() as client:
            issue = await client.get_issue(owner=_OWNER, repo=_REPO, number=1)
        assert issue.number == 1

    @respx.mock
    async def test_create_issue_resolves_label_ids(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/labels").mock(
            return_value=httpx.Response(
                200, json=[{"id": 5, "name": "bug"}, {"id": 6, "name": "wontfix"}]
            ),
        )
        route = respx.post(f"{_FJ}/repos/acme/proj-1/issues").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 2,
                    "title": "x",
                    "state": "open",
                    "user": {"login": "a"},
                },
            ),
        )
        async with _forgejo() as client:
            await client.create_issue(
                owner=_OWNER, repo=_REPO, title=NotBlankStr("x"), labels=("bug",)
            )
        assert b'"labels":[5]' in route.calls.last.request.content

    @respx.mock
    async def test_create_issue_unknown_label_fails_loud(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/labels").mock(
            return_value=httpx.Response(200, json=[{"id": 5, "name": "bug"}]),
        )
        async with _forgejo() as client:
            with pytest.raises(GitBackendForgeApiError):
                await client.create_issue(
                    owner=_OWNER, repo=_REPO, title=NotBlankStr("x"), labels=("nope",)
                )

    @respx.mock
    async def test_review_maps_past_tense_event(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/pulls/7/reviews").mock(
            return_value=httpx.Response(
                200, json={"id": 1, "state": "APPROVED", "user": {"login": "r"}}
            ),
        )
        async with _forgejo() as client:
            await client.review_pull_request(
                owner=_OWNER, repo=_REPO, number=7, decision="approve"
            )
        assert b'"event":"APPROVED"' in route.calls.last.request.content

    @respx.mock
    async def test_merge_posts_do_field_and_empty_body(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/pulls/7/merge").mock(
            return_value=httpx.Response(200),
        )
        async with _forgejo() as client:
            result = await client.merge_pull_request(
                owner=_OWNER, repo=_REPO, number=7, method="squash"
            )
        assert result.merged is True
        assert b'"Do":"squash"' in route.calls.last.request.content

    async def test_draft_pull_request_unsupported(self) -> None:
        async with _forgejo() as client:
            with pytest.raises(FeatureNotImplementedError):
                await client.create_pull_request(
                    owner=_OWNER,
                    repo=_REPO,
                    title=NotBlankStr("t"),
                    source_branch=NotBlankStr("wip"),
                    target_branch=NotBlankStr("main"),
                    draft=True,
                )

    async def test_ci_unsupported(self) -> None:
        async with _forgejo() as client:
            with pytest.raises(FeatureNotImplementedError):
                await client.list_ci_runs(owner=_OWNER, repo=_REPO, limit=5)
            with pytest.raises(FeatureNotImplementedError):
                await client.get_ci_run(owner=_OWNER, repo=_REPO, run_id=1)


class TestAgentFactory:
    @pytest.mark.parametrize(
        ("ctype", "base_url", "expected"),
        [
            (ConnectionType.GITHUB, "https://github.com/acme", GitHubAgentForgeClient),
            (
                ConnectionType.FORGEJO,
                "https://code.example.com/acme",
                ForgejoAgentForgeClient,
            ),
        ],
    )
    def test_builds_supported_forges(
        self, ctype: ConnectionType, base_url: str, expected: type[object]
    ) -> None:
        client = build_forge_agent_api_client(
            connection_type=ctype, base_url=base_url, token="t", timeout=5.0
        )
        assert isinstance(client, expected)

    def test_supported_predicate(self) -> None:
        assert forge_agent_api_supported(ConnectionType.GITHUB) is True
        assert forge_agent_api_supported(ConnectionType.FORGEJO) is True
        assert forge_agent_api_supported(ConnectionType.GITEA) is False
        assert forge_agent_api_supported(ConnectionType.GITLAB) is False

    def test_unsupported_forge_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_forge_agent_api_client(
                connection_type=ConnectionType.GITLAB,
                base_url="https://gitlab.com/acme",
                token="t",
                timeout=5.0,
            )
