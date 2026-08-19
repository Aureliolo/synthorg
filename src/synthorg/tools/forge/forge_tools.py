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

from typing import ClassVar, NamedTuple, override

from pydantic import BaseModel

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeReviewComment,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.forge._args import (
    ForgeCiArgs,
    ForgeIssueArgs,
    ForgePullRequestArgs,
    ForgePushArgs,
    ForgeRepoArgs,
    ForgeReviewCommentArg,
)
from synthorg.tools.forge._base import (
    _TRUNCATED_NOTE,
    ForgeAgentApiClient,
    ToolExecutionResult,
    _BaseForgeTool,
    _json_result,
)
from synthorg.tools.forge._runtime import ForgeToolDeps


def _guard_review_comments(
    comments: tuple[ForgeReviewCommentArg, ...],
) -> tuple[ToolExecutionResult | None, tuple[ForgeReviewComment, ...]]:
    """Enforce the output-style policy on each inline review comment body.

    An inline comment body is agent-authored prose bound for a public
    forge, so it passes the same ``PR_BODY`` guard as every other forge
    text field; a hard-rule violation is rejected before the review posts,
    rather than reaching the forge unfiltered.

    Returns:
        An ``(error, ())`` pair on the first hard block, else
        ``(None, comments)`` with each body policy-approved.
    """
    guarded: list[ForgeReviewComment] = []
    for comment in comments:
        guard = _guard_forge_text(title="", body=comment.body)
        if guard.error is not None:
            return guard.error, ()
        guarded.append(
            ForgeReviewComment(
                path=NotBlankStr(comment.path),
                line=comment.line,
                body=NotBlankStr(guard.body),
                side=comment.side,
            )
        )
    return None, tuple(guarded)


class _ForgeGuard(NamedTuple):
    """Outcome of guarding forge text: an error, or the approved title/body.

    ``error`` is set on a hard block; otherwise ``title`` / ``body`` carry the
    policy-approved text (rewritten when an AUTO_REWRITE rule fired, else the
    original). An empty field passes through unchanged.
    """

    error: ToolExecutionResult | None
    title: str
    body: str


def _guard_forge_text(*, title: str, body: str, is_commit: bool = False) -> _ForgeGuard:
    """Enforce the output-style policy on agent-authored forge text.

    Issue and PR titles/bodies are a ``PR_BODY`` (prose) boundary; a merge
    commit title is a ``COMMIT_MESSAGE`` (code, reject-only) boundary. A
    hard-rule violation (an em-dash) is rejected before the write; an
    AUTO_REWRITE rule's fixed prose is applied so the approved text, never the
    original violating text, reaches the forge. An empty field is skipped.
    Deferred import breaks the tools/engine cold-import cycle.

    Returns:
        A guard carrying an ``error`` on a hard block, else the approved
        (possibly rewritten) ``title`` and ``body``.
    """
    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        approve_texts,
    )

    channel = OutputChannel.COMMIT_MESSAGE if is_commit else OutputChannel.PR_BODY
    approval = approve_texts((title, body), OutputContext(channel=channel))
    if approval.refusal is not None:
        return _ForgeGuard(
            ToolExecutionResult(content=approval.refusal, is_error=True), "", ""
        )
    return _ForgeGuard(None, approval.texts[0], approval.texts[1])


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
            guard = _guard_forge_text(title=args.title, body=args.body)
            if guard.error is not None:
                return guard.error
            issue = await client.create_issue(
                owner=owner,
                repo=repo,
                title=NotBlankStr(guard.title),
                body=guard.body,
                labels=args.labels,
            )
            return _json_result(issue.model_dump(mode="json"))
        guard = _guard_forge_text(title="", body=args.body)
        if guard.error is not None:
            return guard.error
        comment = await client.comment_issue(
            owner=owner, repo=repo, number=args.number, body=NotBlankStr(guard.body)
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
            guard = _guard_forge_text(title=args.title, body=args.body)
            if guard.error is not None:
                return guard.error
            pull = await client.create_pull_request(
                owner=owner,
                repo=repo,
                title=NotBlankStr(guard.title),
                source_branch=NotBlankStr(args.source_branch),
                target_branch=NotBlankStr(args.target_branch),
                body=guard.body,
                draft=args.draft,
            )
            return _json_result(pull.model_dump(mode="json"))
        if args.action == "comment":
            guard = _guard_forge_text(title="", body=args.body)
            if guard.error is not None:
                return guard.error
            comment = await client.comment_pull_request(
                owner=owner, repo=repo, number=args.number, body=NotBlankStr(guard.body)
            )
            return _json_result(comment.model_dump(mode="json"))
        if args.action == "review":
            guard = _guard_forge_text(title="", body=args.body)
            if guard.error is not None:
                return guard.error
            comment_error, review_comments = _guard_review_comments(args.comments)
            if comment_error is not None:
                return comment_error
            review = await client.review_pull_request(
                owner=owner,
                repo=repo,
                number=args.number,
                decision=args.decision,
                body=guard.body,
                comments=review_comments,
            )
            return _json_result(review.model_dump(mode="json"))
        guard = _guard_forge_text(title=args.commit_title, body="", is_commit=True)
        if guard.error is not None:
            return guard.error
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=args.number,
            method=args.method,
            commit_title=guard.title,
        )
        return _json_result(result.model_dump(mode="json"))


class ForgeCiTool(_BaseForgeTool):
    """Read, trigger, or re-run CI runs for a repository.

    CI operations are available only when the bound forge exposes a
    CI/pipeline API; a forge without one raises ``ForgeUnsupportedError``
    at the boundary. Triggering and re-running are writes and route
    through the approval gate.
    """

    args_model: ClassVar[type[BaseModel] | None] = ForgeCiArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_ci",
            description=(
                "Work with continuous-integration runs for the bound forge"
                " repository: list runs (list_runs, optional branch), get a single"
                " run (get_run, requires run_id), trigger a run (trigger, requires"
                " workflow + branch), or re-run one (rerun, requires run_id)."
                " Triggering and re-running require approval. Available when the"
                " bound forge connection exposes a CI API."
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
        if args.action == "trigger":
            trigger = await client.trigger_ci_run(
                owner=owner,
                repo=repo,
                workflow=NotBlankStr(args.workflow),
                branch=NotBlankStr(args.branch),
            )
            return _json_result(trigger.model_dump(mode="json"))
        if args.action == "rerun":
            trigger = await client.rerun_ci_run(
                owner=owner, repo=repo, run_id=args.run_id
            )
            return _json_result(trigger.model_dump(mode="json"))
        runs = await client.list_ci_runs(
            owner=owner, repo=repo, branch=args.branch or None, limit=args.limit
        )
        return _json_result([r.model_dump(mode="json") for r in runs])


class ForgePushTool(_BaseForgeTool):
    """Create a branch or write a file to the bound forge repository.

    This is the forge-API on-ramp for an agent landing its own work: open
    a feature branch, then commit file contents to it (before opening a
    pull request with ``forge_pull_request``). Both actions mutate the
    remote, so the tool binds its own ``ActionType.VCS_PUSH`` (never the
    shared ``comms:external``) and is governed by the git-access
    sub-constraint and the approval gate.
    """

    args_model: ClassVar[type[BaseModel] | None] = ForgePushArgs
    _ACTION_TYPE: ClassVar[str] = ActionType.VCS_PUSH.value

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_push",
            description=(
                "Land work on the bound forge repository: create a branch"
                " (create_branch, requires new_branch + from_ref) or write a file"
                " (write_file, requires path + branch + message; pass sha to update"
                " an existing file). Then open a pull request with"
                " forge_pull_request. Writes require approval."
            ),
            args_model=ForgePushArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ForgePushArgs)  # noqa: S101 -- parsed by execute
        owner, repo = NotBlankStr(args.owner), NotBlankStr(args.repo)
        if args.action == "create_branch":
            branch = await client.create_branch(
                owner=owner,
                repo=repo,
                new_branch=NotBlankStr(args.new_branch),
                from_ref=NotBlankStr(args.from_ref),
            )
            return _json_result(branch.model_dump(mode="json"))
        guard = _guard_forge_text(title=args.message, body="", is_commit=True)
        if guard.error is not None:
            return guard.error
        commit = await client.write_file(
            owner=owner,
            repo=repo,
            path=NotBlankStr(args.path),
            content=args.content,
            branch=NotBlankStr(args.branch),
            message=NotBlankStr(guard.title),
            sha=args.sha or None,
        )
        return _json_result(commit.model_dump(mode="json"))


__all__ = [
    "ForgeCiTool",
    "ForgeIssueTool",
    "ForgePullRequestTool",
    "ForgePushTool",
    "ForgeRepoTool",
]
