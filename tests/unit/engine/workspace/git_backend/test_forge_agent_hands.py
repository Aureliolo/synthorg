"""Unit tests for the agent-hands forge operations across all clients.

Covers the operations added for the agent-hands work: ``create_branch``,
``write_file``, ``list_accessible_repos``, inline (diff-anchored) review
comments, and CI trigger / re-run, exercising the GitHub, Gitea/Forgejo,
and GitLab divergences against ``respx``-mocked HTTP (no live forge).
"""

import base64

import httpx
import pytest
import respx

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
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
    async def test_create_branch_annotated_tag_ref_sends_empty_sha(self) -> None:
        # An annotated tag resolves to a ref with no ``object`` sha; the
        # client sends an empty source sha and lets the forge decide.
        respx.get(f"{_GH}/repos/acme/proj-1/git/ref/heads/main").mock(
            return_value=httpx.Response(
                200, json={"ref": "refs/heads/main", "object": None}
            ),
        )
        create = respx.post(f"{_GH}/repos/acme/proj-1/git/refs").mock(
            return_value=httpx.Response(
                201,
                json={"ref": "refs/heads/feature", "object": {"sha": "new-sha"}},
            ),
        )
        async with _github() as client:
            branch = await client.create_branch(
                owner=_OWNER,
                repo=_REPO,
                new_branch=NotBlankStr("feature"),
                from_ref=NotBlankStr("main"),
            )
        assert branch.sha == "new-sha"
        assert b'"sha":""' in create.calls.last.request.content

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
    async def test_list_accessible_repos_pages_past_the_page_cap(self) -> None:
        """A scope wider than one page keeps paging until the limit.

        The forge caps a page at 100 entries, so a single request would
        silently truncate the operator's picker to the first 100
        repositories and make the rest unselectable.
        """

        def _page(start: int, count: int) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": f"acme/proj-{index}",
                        "private": False,
                        "permissions": {"admin": False, "push": False, "pull": True},
                    }
                    for index in range(start, start + count)
                ],
            )

        route = respx.get(f"{_GH}/user/repos").mock(
            side_effect=[_page(0, 100), _page(100, 50)],
        )
        async with _github() as client:
            repos = await client.list_accessible_repos(limit=150)
        assert len(repos) == 150
        assert route.call_count == 2
        assert str(repos[-1].repo) == "proj-149"

    @respx.mock
    async def test_list_accessible_repos_stops_on_short_page(self) -> None:
        """A page smaller than requested is the last one; stop asking."""
        route = respx.get(f"{_GH}/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "full_name": "acme/proj-1",
                        "private": False,
                        "permissions": {"admin": False, "push": False, "pull": True},
                    }
                ],
            ),
        )
        async with _github() as client:
            repos = await client.list_accessible_repos(limit=150)
        assert len(repos) == 1
        assert route.call_count == 1

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

    @respx.mock
    async def test_create_branch_missing_commit_id_raises(self) -> None:
        respx.post(f"{_FJ}/repos/acme/proj-1/branches").mock(
            return_value=httpx.Response(201, json={"name": "feature"}),
        )
        async with _forgejo() as client:
            with pytest.raises(GitBackendForgeApiError, match="commit id"):
                await client.create_branch(
                    owner=_OWNER,
                    repo=_REPO,
                    new_branch=NotBlankStr("feature"),
                    from_ref=NotBlankStr("main"),
                )


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
    async def test_list_accessible_repos_pages_past_the_page_cap(self) -> None:
        def _page(start: int, count: int) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "path_with_namespace": f"acme/proj-{index}",
                        "visibility": "private",
                        "permissions": {"project_access": {"access_level": 30}},
                    }
                    for index in range(start, start + count)
                ],
            )

        route = respx.get(f"{_GL}/projects").mock(
            side_effect=[_page(0, 100), _page(100, 20)],
        )
        async with _gitlab() as client:
            repos = await client.list_accessible_repos(limit=120)
        assert len(repos) == 120
        assert route.call_count == 2

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

    @respx.mock
    async def test_read_file_with_ref(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/files/src%2Fx.py").mock(
            return_value=httpx.Response(
                200,
                json={
                    "file_path": "src/x.py",
                    "ref": "main",
                    "encoding": "base64",
                    "content": base64.b64encode(b"hello\n").decode(),
                    "blob_id": "blob1",
                    "size": 6,
                },
            ),
        )
        async with _gitlab() as client:
            file = await client.read_file(
                owner=_OWNER,
                repo=_REPO,
                path=NotBlankStr("src/x.py"),
                ref="main",
            )
        assert file.content == "hello\n"
        assert file.ref == "main"

    @respx.mock
    async def test_read_file_resolves_default_branch(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "path_with_namespace": "acme/proj-1",
                    "default_branch": "trunk",
                    "visibility": "private",
                    "http_url_to_repo": "https://gitlab.example.com/acme/proj-1.git",
                },
            ),
        )
        files = respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/files/x.py").mock(
            return_value=httpx.Response(
                200,
                json={
                    "file_path": "x.py",
                    "ref": "trunk",
                    "encoding": "base64",
                    "content": base64.b64encode(b"y=1\n").decode(),
                    "blob_id": "b2",
                    "size": 4,
                },
            ),
        )
        async with _gitlab() as client:
            file = await client.read_file(
                owner=_OWNER, repo=_REPO, path=NotBlankStr("x.py")
            )
        assert file.content == "y=1\n"
        # The default branch was resolved and used as the ref.
        assert files.calls.last.request.url.params["ref"] == "trunk"

    @respx.mock
    async def test_read_file_bad_encoding_raises(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/files/x.py").mock(
            return_value=httpx.Response(
                200,
                json={"file_path": "x.py", "ref": "main", "encoding": "text"},
            ),
        )
        async with _gitlab() as client:
            with pytest.raises(GitBackendForgeApiError, match="not a readable file"):
                await client.read_file(
                    owner=_OWNER, repo=_REPO, path=NotBlankStr("x.py"), ref="main"
                )

    @respx.mock
    async def test_list_dir(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/tree").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "e1", "name": "src", "type": "tree", "path": "src"},
                    {"id": "e2", "name": "a.py", "type": "blob", "path": "a.py"},
                ],
            ),
        )
        async with _gitlab() as client:
            entries = await client.list_dir(owner=_OWNER, repo=_REPO)
        assert [e.kind for e in entries] == ["dir", "file"]

    @respx.mock
    async def test_list_dir_non_array_raises(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/repository/tree").mock(
            return_value=httpx.Response(200, json={"not": "a list"}),
        )
        async with _gitlab() as client:
            with pytest.raises(GitBackendForgeApiError, match="collection"):
                await client.list_dir(owner=_OWNER, repo=_REPO)

    @respx.mock
    async def test_get_and_list_issues(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/issues/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "iid": 5,
                    "title": "bug",
                    "state": "opened",
                    "description": "boom",
                    "author": {"username": "dev"},
                    "web_url": "https://gitlab.example.com/acme/proj-1/-/issues/5",
                },
            ),
        )
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/issues").mock(
            return_value=httpx.Response(
                200, json=[{"iid": 5, "title": "bug", "state": "opened"}]
            ),
        )
        async with _gitlab() as client:
            issue = await client.get_issue(owner=_OWNER, repo=_REPO, number=5)
            issues = await client.list_issues(owner=_OWNER, repo=_REPO, limit=10)
        assert issue.number == 5
        assert issue.state == "open"
        assert len(issues) == 1

    @respx.mock
    async def test_malformed_issue_iid_raises(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/issues/9").mock(
            return_value=httpx.Response(
                200, json={"iid": 0, "title": "x", "state": "opened"}
            ),
        )
        async with _gitlab() as client:
            with pytest.raises(GitBackendForgeApiError, match="malformed"):
                await client.get_issue(owner=_OWNER, repo=_REPO, number=9)

    @respx.mock
    async def test_create_and_comment_issue(self) -> None:
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/issues").mock(
            return_value=httpx.Response(
                201, json={"iid": 7, "title": "new", "state": "opened"}
            ),
        )
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/issues/7/notes").mock(
            return_value=httpx.Response(
                201, json={"id": 1, "body": "note", "author": {"username": "bot"}}
            ),
        )
        async with _gitlab() as client:
            issue = await client.create_issue(
                owner=_OWNER,
                repo=_REPO,
                title=NotBlankStr("new"),
                body="desc",
                labels=("bug",),
            )
            comment = await client.comment_issue(
                owner=_OWNER, repo=_REPO, number=7, body=NotBlankStr("note")
            )
        assert issue.number == 7
        assert comment.body == "note"

    @respx.mock
    async def test_get_list_comment_merge_pull_request(self) -> None:
        mr_json = {
            "iid": 3,
            "title": "feat",
            "state": "opened",
            "author": {"username": "dev"},
            "web_url": "https://gitlab.example.com/acme/proj-1/-/merge_requests/3",
            "source_branch": "feature",
            "target_branch": "main",
        }
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/3").mock(
            return_value=httpx.Response(200, json=mr_json)
        )
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/merge_requests").mock(
            return_value=httpx.Response(200, json=[mr_json])
        )
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/3/notes").mock(
            return_value=httpx.Response(
                201, json={"id": 2, "body": "hi", "author": {"username": "bot"}}
            ),
        )
        respx.put(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/3/merge").mock(
            return_value=httpx.Response(
                200, json={**mr_json, "state": "merged", "merge_commit_sha": "m-sha"}
            ),
        )
        async with _gitlab() as client:
            pull = await client.get_pull_request(owner=_OWNER, repo=_REPO, number=3)
            pulls = await client.list_pull_requests(owner=_OWNER, repo=_REPO, limit=5)
            comment = await client.comment_pull_request(
                owner=_OWNER, repo=_REPO, number=3, body=NotBlankStr("hi")
            )
            merge = await client.merge_pull_request(
                owner=_OWNER, repo=_REPO, number=3, method="squash"
            )
        assert pull.number == 3
        assert pull.source_branch == "feature"
        assert len(pulls) == 1
        assert comment.body == "hi"
        assert merge.merged is True
        assert merge.sha == "m-sha"

    @respx.mock
    async def test_list_get_rerun_ci(self) -> None:
        pipeline = {
            "id": 42,
            "status": "success",
            "ref": "main",
            "sha": "s",
            "web_url": "https://gitlab.example.com/acme/proj-1/-/pipelines/42",
        }
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/pipelines").mock(
            return_value=httpx.Response(200, json=[pipeline])
        )
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/pipelines/42").mock(
            return_value=httpx.Response(200, json=pipeline)
        )
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/pipelines/42/retry").mock(
            return_value=httpx.Response(201, json={**pipeline, "status": "running"})
        )
        async with _gitlab() as client:
            runs = await client.list_ci_runs(owner=_OWNER, repo=_REPO, limit=5)
            run = await client.get_ci_run(owner=_OWNER, repo=_REPO, run_id=42)
            rerun = await client.rerun_ci_run(owner=_OWNER, repo=_REPO, run_id=42)
        assert len(runs) == 1
        assert run.id == 42
        assert rerun.triggered is True
        assert rerun.run is not None

    @respx.mock
    async def test_review_inline_comments_diff_anchored(self) -> None:
        mr_json = {
            "iid": 8,
            "title": "feat",
            "state": "opened",
            "source_branch": "f",
            "target_branch": "main",
            "diff_refs": {
                "base_sha": "b",
                "head_sha": "h",
                "start_sha": "s",
            },
        }
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/8").mock(
            return_value=httpx.Response(200, json=mr_json)
        )
        discussions = respx.post(
            f"{_GL}/projects/{_GL_PROJECT}/merge_requests/8/discussions"
        ).mock(return_value=httpx.Response(201, json={}))
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/8/notes").mock(
            return_value=httpx.Response(
                201, json={"id": 3, "body": "note", "author": {"username": "bot"}}
            ),
        )
        async with _gitlab() as client:
            review = await client.review_pull_request(
                owner=_OWNER,
                repo=_REPO,
                number=8,
                decision="comment",
                body="note",
                comments=(
                    ForgeReviewComment(
                        path=NotBlankStr("a.py"),
                        line=4,
                        body=NotBlankStr("base bug"),
                        side="LEFT",
                    ),
                ),
            )
        assert review.comment_count == 1
        # A LEFT-side comment anchors to old_path/old_line in the position.
        sent = discussions.calls.last.request.content
        assert b'"old_path"' in sent
        assert b'"old_line":4' in sent

    @respx.mock
    async def test_review_inline_comments_without_diff_refs_unsupported(self) -> None:
        respx.get(f"{_GL}/projects/{_GL_PROJECT}/merge_requests/9").mock(
            return_value=httpx.Response(
                200,
                json={"iid": 9, "title": "x", "state": "opened", "diff_refs": None},
            ),
        )
        async with _gitlab() as client:
            with pytest.raises(FeatureNotImplementedError, match="diff refs"):
                await client.review_pull_request(
                    owner=_OWNER,
                    repo=_REPO,
                    number=9,
                    decision="comment",
                    body="x",
                    comments=(
                        ForgeReviewComment(
                            path=NotBlankStr("a.py"), line=1, body=NotBlankStr("c")
                        ),
                    ),
                )

    @respx.mock
    async def test_create_branch_missing_commit_id_raises(self) -> None:
        respx.post(f"{_GL}/projects/{_GL_PROJECT}/repository/branches").mock(
            return_value=httpx.Response(201, json={"name": "feature"}),
        )
        async with _gitlab() as client:
            with pytest.raises(GitBackendForgeApiError, match="commit id"):
                await client.create_branch(
                    owner=_OWNER,
                    repo=_REPO,
                    new_branch=NotBlankStr("feature"),
                    from_ref=NotBlankStr("main"),
                )
