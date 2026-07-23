"""Unit tests for the agent-hands forge operations across all clients.

Covers the operations added for the agent-hands work: ``create_branch``,
``write_file``, ``list_accessible_repos``, inline (diff-anchored) review
comments, and CI trigger / re-run, exercising the GitHub, Gitea/Forgejo,
and GitLab divergences against ``respx``-mocked HTTP (no live forge).
"""

import httpx
import pytest
import respx

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeReviewComment,
)
from synthorg.engine.workspace.git_backend.forge_api.gitea_agent import (
    ForgejoAgentForgeClient,
)
from synthorg.engine.workspace.git_backend.forge_api.github_agent import (
    GitHubAgentForgeClient,
)
from synthorg.engine.workspace.git_backend.forge_api.gitlab_agent import (
    GitLabAgentForgeClient,
)

pytestmark = pytest.mark.unit

_OWNER = NotBlankStr("acme")
_REPO = NotBlankStr("proj-1")
_GH = "https://api.github.com"
_FJ = "https://code.example.com/api/v1"
_GL = "https://gitlab.example.com/api/v4"
_GL_PROJECT = "acme%2Fproj-1"


def _github() -> GitHubAgentForgeClient:
    return GitHubAgentForgeClient(api_base_url=_GH, token="t0ken", timeout=5.0)


def _forgejo() -> ForgejoAgentForgeClient:
    return ForgejoAgentForgeClient(api_base_url=_FJ, token="t0ken", timeout=5.0)


def _gitlab() -> GitLabAgentForgeClient:
    return GitLabAgentForgeClient(api_base_url=_GL, token="t0ken", timeout=5.0)


class TestGitHubHands:
    @respx.mock
    async def test_create_branch_resolves_then_creates(self) -> None:
        respx.get(f"{_GH}/repos/acme/proj-1/git/ref/heads/main").mock(
            return_value=httpx.Response(
                200, json={"ref": "refs/heads/main", "object": {"sha": "base-sha"}}
            ),
        )
        create = respx.post(f"{_GH}/repos/acme/proj-1/git/refs").mock(
            return_value=httpx.Response(
                201,
                json={"ref": "refs/heads/feature", "object": {"sha": "base-sha"}},
            ),
        )
        async with _github() as client:
            branch = await client.create_branch(
                owner=_OWNER,
                repo=_REPO,
                new_branch=NotBlankStr("feature"),
                from_ref=NotBlankStr("main"),
            )
        assert str(branch.name) == "feature"
        assert branch.sha == "base-sha"
        assert create.called

    @respx.mock
    async def test_write_file(self) -> None:
        respx.put(f"{_GH}/repos/acme/proj-1/contents/src/x.py").mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"path": "src/x.py", "sha": "blob-sha"},
                    "commit": {"sha": "commit-sha"},
                },
            ),
        )
        async with _github() as client:
            commit = await client.write_file(
                owner=_OWNER,
                repo=_REPO,
                path=NotBlankStr("src/x.py"),
                content="print('hi')\n",
                branch=NotBlankStr("feature"),
                message=NotBlankStr("add x"),
            )
        assert str(commit.path) == "src/x.py"
        assert commit.commit_sha == "commit-sha"
        assert commit.content_sha == "blob-sha"

    @respx.mock
    async def test_list_accessible_repos_maps_permission(self) -> None:
        respx.get(f"{_GH}/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "full_name": "acme/proj-1",
                        "private": True,
                        "permissions": {"admin": False, "push": True, "pull": True},
                    },
                    {"full_name": "malformed-no-slash"},
                ],
            ),
        )
        async with _github() as client:
            repos = await client.list_accessible_repos(limit=50)
        # The malformed entry (no owner/repo) is skipped, not fatal.
        assert len(repos) == 1
        assert str(repos[0].owner) == "acme"
        assert str(repos[0].repo) == "proj-1"
        assert repos[0].permission == "write"

    @respx.mock
    async def test_review_with_inline_comments(self) -> None:
        route = respx.post(f"{_GH}/repos/acme/proj-1/pulls/7/reviews").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 99,
                    "state": "CHANGES_REQUESTED",
                    "user": {"login": "bot"},
                    "body": "please fix",
                    "html_url": "https://github.com/acme/proj-1/pull/7",
                },
            ),
        )
        async with _github() as client:
            review = await client.review_pull_request(
                owner=_OWNER,
                repo=_REPO,
                number=7,
                decision="request_changes",
                body="please fix",
                comments=(
                    ForgeReviewComment(
                        path=NotBlankStr("src/x.py"), line=3, body=NotBlankStr("bug")
                    ),
                ),
            )
        assert review.comment_count == 1
        sent = route.calls.last.request
        assert b'"comments"' in sent.content
        assert b'"line":3' in sent.content

    @respx.mock
    async def test_trigger_and_rerun(self) -> None:
        trig = respx.post(
            f"{_GH}/repos/acme/proj-1/actions/workflows/ci.yml/dispatches"
        ).mock(return_value=httpx.Response(204))
        rerun = respx.post(f"{_GH}/repos/acme/proj-1/actions/runs/12/rerun").mock(
            return_value=httpx.Response(201)
        )
        async with _github() as client:
            t = await client.trigger_ci_run(
                owner=_OWNER,
                repo=_REPO,
                workflow=NotBlankStr("ci.yml"),
                branch=NotBlankStr("feature"),
            )
            r = await client.rerun_ci_run(owner=_OWNER, repo=_REPO, run_id=12)
        assert t.triggered is True
        assert t.run is None
        assert r.triggered is True
        assert trig.called
        assert rerun.called


class TestGiteaHands:
    @respx.mock
    async def test_create_branch_one_shot(self) -> None:
        respx.post(f"{_FJ}/repos/acme/proj-1/branches").mock(
            return_value=httpx.Response(
                201, json={"name": "feature", "commit": {"id": "gitea-sha"}}
            ),
        )
        async with _forgejo() as client:
            branch = await client.create_branch(
                owner=_OWNER,
                repo=_REPO,
                new_branch=NotBlankStr("feature"),
                from_ref=NotBlankStr("main"),
            )
        assert str(branch.name) == "feature"
        assert branch.sha == "gitea-sha"

    @respx.mock
    async def test_review_inline_comment_new_position(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/pulls/4/reviews").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 5,
                    "state": "COMMENT",
                    "user": {"login": "bot"},
                    "body": "note",
                    "html_url": "https://code.example.com/acme/proj-1/pulls/4",
                },
            ),
        )
        async with _forgejo() as client:
            await client.review_pull_request(
                owner=_OWNER,
                repo=_REPO,
                number=4,
                decision="comment",
                body="note",
                comments=(
                    ForgeReviewComment(
                        path=NotBlankStr("a.py"), line=2, body=NotBlankStr("x")
                    ),
                ),
            )
        assert b'"new_position":2' in route.calls.last.request.content

    async def test_ci_trigger_unsupported(self) -> None:
        async with _forgejo() as client:
            with pytest.raises(FeatureNotImplementedError):
                await client.trigger_ci_run(
                    owner=_OWNER,
                    repo=_REPO,
                    workflow=NotBlankStr("ci"),
                    branch=NotBlankStr("main"),
                )
            with pytest.raises(FeatureNotImplementedError):
                await client.rerun_ci_run(owner=_OWNER, repo=_REPO, run_id=1)


class TestGitLabHands:
    @respx.mock
    async def test_get_repo(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "path_with_namespace": "acme/proj-1",
                    "default_branch": "main",
                    "visibility": "private",
                    "http_url_to_repo": "https://gitlab.example.com/acme/proj-1.git",
                },
            ),
        )
        async with _gitlab() as client:
            repo = await client.get_repo(owner=_OWNER, repo=_REPO)
        assert str(repo.full_name) == "acme/proj-1"
        assert repo.private is True

    @respx.mock
    async def test_create_branch(self) -> None:
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/repository/branches").mock(
            return_value=httpx.Response(
                201, json={"name": "feature", "commit": {"id": "gl-sha"}}
            ),
        )
        async with _gitlab() as client:
            branch = await client.create_branch(
                owner=_OWNER,
                repo=_REPO,
                new_branch=NotBlankStr("feature"),
                from_ref=NotBlankStr("main"),
            )
        assert branch.sha == "gl-sha"

    @respx.mock
    async def test_write_file_resolves_commit_sha(self) -> None:
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/repository/files/x.py").mock(
            return_value=httpx.Response(
                201, json={"file_path": "x.py", "branch": "feature"}
            ),
        )
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/branches/feature").mock(
            return_value=httpx.Response(
                200, json={"name": "feature", "commit": {"id": "head-sha"}}
            ),
        )
        async with _gitlab() as client:
            commit = await client.write_file(
                owner=_OWNER,
                repo=_REPO,
                path=NotBlankStr("x.py"),
                content="x=1\n",
                branch=NotBlankStr("feature"),
                message=NotBlankStr("add x"),
            )
        assert commit.commit_sha == "head-sha"
        assert str(commit.branch) == "feature"

    @respx.mock
    async def test_list_accessible_repos_admin(self) -> None:
        respx.get(f"{_GL}/projects").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "path_with_namespace": "acme/proj-1",
                        "visibility": "private",
                        "permissions": {"project_access": {"access_level": 40}},
                    }
                ],
            ),
        )
        async with _gitlab() as client:
            repos = await client.list_accessible_repos(limit=20)
        assert repos[0].permission == "admin"

    @respx.mock
    async def test_review_approves_and_notes(self) -> None:
        note = respx.post(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/3/notes").mock(
            return_value=httpx.Response(
                201, json={"id": 1, "body": "lgtm", "author": {"username": "bot"}}
            ),
        )
        approve = respx.post(
            f"{_GL}/projects/{_GL_PROJECT}/merge_requests/3/approve"
        ).mock(return_value=httpx.Response(201, json={}))
        async with _gitlab() as client:
            review = await client.review_pull_request(
                owner=_OWNER, repo=_REPO, number=3, decision="approve", body="lgtm"
            )
        assert review.state == "approve"
        assert note.called
        assert approve.called

    @respx.mock
    async def test_trigger_pipeline(self) -> None:
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/pipeline").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": 42,
                    "status": "created",
                    "ref": "feature",
                    "sha": "sha",
                    "web_url": "https://gitlab.example.com/acme/proj-1/-/pipelines/42",
                },
            ),
        )
        async with _gitlab() as client:
            trigger = await client.trigger_ci_run(
                owner=_OWNER,
                repo=_REPO,
                workflow=NotBlankStr("unused"),
                branch=NotBlankStr("feature"),
            )
        assert trigger.triggered is True
        assert trigger.run is not None
        assert trigger.run.id == 42
