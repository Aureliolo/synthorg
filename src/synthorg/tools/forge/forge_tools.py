"""Resource-grouped forge agent tools.

Vendor-neutral tools (``forge_repo`` / ``forge_issue`` /
``forge_pull_request`` / ``forge_ci``) that resolve a bound forge
connection, dispatch through the connection-type-keyed
``forge_agent_api_client`` registry, and route every write through the
shared approval gate (``COMMS_EXTERNAL``). Egress is pinned to the
connection's host by construction, so the agent can never redirect a
call to another host. The shared resolve / gate / dispatch / error-map
machinery lives in :mod:`synthorg.tools.forge._base`.
"""

from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.types import NotBlankStr
from synthorg.tools.forge._args import (
    ForgeCiArgs,
    ForgeIssueArgs,
    ForgePullRequestArgs,
    ForgeRepoArgs,
)
from synthorg.tools.forge._base import (
    _TRUNCATED_NOTE,
    ForgeAgentApiClient,
    ToolExecutionResult,
    _BaseForgeTool,
    _json_result,
)
from synthorg.tools.forge._runtime import ForgeToolDeps


class ForgeRepoTool(_BaseForgeTool):
    """Read repository metadata, a file, or a directory listing."""

    args_model: ClassVar[type[BaseModel] | None] = ForgeRepoArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_repo",
            description=(
                "Read from the bound forge repository: get repo metadata"
                " (get_repo), read a file (read_file, requires path), or list a"
                " directory (list_dir). Provide owner + repo and an optional ref."
            ),
            args_model=ForgeRepoArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ForgeRepoArgs)  # noqa: S101 -- parsed by execute
        owner, repo = NotBlankStr(args.owner), NotBlankStr(args.repo)
        ref = args.ref or None
        if args.action == "get_repo":
            repo_model = await client.get_repo(owner=owner, repo=repo)
            return _json_result(repo_model.model_dump(mode="json"))
        if args.action == "read_file":
            file = await client.read_file(
                owner=owner, repo=repo, path=NotBlankStr(args.path), ref=ref
            )
            return self._file_result(file.content, str(file.path), file.ref)
        entries = await client.list_dir(owner=owner, repo=repo, path=args.path, ref=ref)
        return _json_result([e.model_dump(mode="json") for e in entries])

    def _file_result(self, content: str, path: str, ref: str) -> ToolExecutionResult:
        truncated = len(content) > self._runtime.max_read_chars
        body = (
            content[: self._runtime.max_read_chars] + _TRUNCATED_NOTE
            if truncated
            else content
        )
        return ToolExecutionResult(
            content=body,
            metadata={"path": path, "ref": ref, "truncated": truncated},
        )


class ForgeIssueTool(_BaseForgeTool):
    """Read, open, or comment on issues."""

    args_model: ClassVar[type[BaseModel] | None] = ForgeIssueArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_issue",
            description=(
                "Work with forge issues: get a single issue (get), list issues"
                " (list), open a new issue (open, requires title), or add a comment"
                " (comment, requires number + body). Writes require approval."
            ),
            args_model=ForgeIssueArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ForgeIssueArgs)  # noqa: S101 -- parsed by execute
        owner, repo = NotBlankStr(args.owner), NotBlankStr(args.repo)
        if args.action == "get":
            issue = await client.get_issue(owner=owner, repo=repo, number=args.number)
            return _json_result(issue.model_dump(mode="json"))
        if args.action == "list":
            issues = await client.list_issues(
                owner=owner, repo=repo, state=args.state, limit=args.limit
            )
            return _json_result([i.model_dump(mode="json") for i in issues])
        if args.action == "open":
            issue = await client.create_issue(
                owner=owner,
                repo=repo,
                title=NotBlankStr(args.title),
                body=args.body,
                labels=args.labels,
            )
            return _json_result(issue.model_dump(mode="json"))
        comment = await client.comment_issue(
            owner=owner, repo=repo, number=args.number, body=NotBlankStr(args.body)
        )
        return _json_result(comment.model_dump(mode="json"))


class ForgePullRequestTool(_BaseForgeTool):
    """Read, open, comment on, review, or merge pull requests."""

    args_model: ClassVar[type[BaseModel] | None] = ForgePullRequestArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_pull_request",
            description=(
                "Work with forge pull requests: get (get), list (list), open (open,"
                " requires title + source_branch + target_branch), comment"
                " (comment), review (review, decision=approve|request_changes|"
                "comment), or merge (merge, method=merge|squash|rebase). Writes"
                " require approval."
            ),
            args_model=ForgePullRequestArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ForgePullRequestArgs)  # noqa: S101 -- parsed by execute
        owner, repo = NotBlankStr(args.owner), NotBlankStr(args.repo)
        if args.action == "get":
            pull = await client.get_pull_request(
                owner=owner, repo=repo, number=args.number
            )
            return _json_result(pull.model_dump(mode="json"))
        if args.action == "list":
            pulls = await client.list_pull_requests(
                owner=owner, repo=repo, state=args.state, limit=args.limit
            )
            return _json_result([p.model_dump(mode="json") for p in pulls])
        if args.action == "open":
            pull = await client.create_pull_request(
                owner=owner,
                repo=repo,
                title=NotBlankStr(args.title),
                source_branch=NotBlankStr(args.source_branch),
                target_branch=NotBlankStr(args.target_branch),
                body=args.body,
                draft=args.draft,
            )
            return _json_result(pull.model_dump(mode="json"))
        if args.action == "comment":
            comment = await client.comment_pull_request(
                owner=owner, repo=repo, number=args.number, body=NotBlankStr(args.body)
            )
            return _json_result(comment.model_dump(mode="json"))
        if args.action == "review":
            review = await client.review_pull_request(
                owner=owner,
                repo=repo,
                number=args.number,
                decision=args.decision,
                body=args.body,
            )
            return _json_result(review.model_dump(mode="json"))
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=args.number,
            method=args.method,
            commit_title=args.commit_title,
        )
        return _json_result(result.model_dump(mode="json"))


class ForgeCiTool(_BaseForgeTool):
    """Read CI runs for a repository.

    CI reads are available only when the bound forge exposes a CI-run
    API; a forge without one raises ``ForgeUnsupportedError`` at the
    boundary.
    """

    args_model: ClassVar[type[BaseModel] | None] = ForgeCiArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_ci",
            description=(
                "Read continuous-integration runs for the bound forge repository:"
                " list runs (list_runs, optional branch) or get a single run"
                " (get_run, requires run_id). Available when the bound forge"
                " connection exposes CI-run reads."
            ),
            args_model=ForgeCiArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ForgeCiArgs)  # noqa: S101 -- parsed by execute
        owner, repo = NotBlankStr(args.owner), NotBlankStr(args.repo)
        if args.action == "get_run":
            run = await client.get_ci_run(owner=owner, repo=repo, run_id=args.run_id)
            return _json_result(run.model_dump(mode="json"))
        runs = await client.list_ci_runs(
            owner=owner, repo=repo, branch=args.branch or None, limit=args.limit
        )
        return _json_result([r.model_dump(mode="json") for r in runs])


__all__ = [
    "ForgeCiTool",
    "ForgeIssueTool",
    "ForgePullRequestTool",
    "ForgeRepoTool",
]
