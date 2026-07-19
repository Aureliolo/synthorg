"""Resource-grouped forge agent tools.

Vendor-neutral tools (``forge_repo`` / ``forge_issue`` /
``forge_pull_request`` / ``forge_ci``) that resolve a bound forge
connection, dispatch through the connection-type-keyed
``forge_agent_api_client`` registry, and route every write through the
shared approval gate (``COMMS_EXTERNAL``). Egress is pinned to the
connection's host by construction, so the agent can never redirect a
call to another host.
"""

import json
from abc import ABC, abstractmethod
from typing import ClassVar, override

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendRateLimitError,
)
from synthorg.engine.workspace.git_backend.forge_api import (
    ForgeAgentApiClient,
    build_forge_agent_api_client,
    forge_agent_api_supported,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import Connection
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import FORGE_TOOL_CREDENTIAL_FAILED
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools._governed_action import ActionSignature, ConnectionApprovalGate
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.forge._args import (
    ForgeCiArgs,
    ForgeIssueArgs,
    ForgePullRequestArgs,
    ForgeRepoArgs,
)
from synthorg.tools.forge._runtime import ForgeToolDeps
from synthorg.tools.forge.errors import (
    ForgeConnectionNotFoundError,
    ForgeCredentialError,
    ForgeRateLimitedError,
    ForgeToolArgumentError,
    ForgeToolError,
    ForgeUnsupportedError,
    ForgeUpstreamError,
)

logger = get_logger(__name__)

_ACTION_TYPE = ActionType.COMMS_EXTERNAL.value
_TRUNCATED_NOTE = "\n... [truncated]"


class _BaseForgeTool(BaseTool, ABC):
    """Shared connection-resolution + approval gating for the forge tools."""

    args_model: ClassVar[type[BaseModel] | None] = None

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        deps: ForgeToolDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.EXTERNAL_DATA,
            action_type=_ACTION_TYPE,
            parameters_schema=args_model.model_json_schema(),
        )
        self._runtime = deps.runtime
        self._gate = ConnectionApprovalGate(
            approval_store=deps.approval_store,
            agent_id=deps.agent_id,
            task_id=deps.task_id,
            action_type=_ACTION_TYPE,
            effective_autonomy=deps.effective_autonomy,
            risk_classifier=deps.risk_classifier,
            clock=deps.clock,
        )

    @property
    def _catalog(self) -> ConnectionCatalog:
        return self._runtime.connection_catalog

    async def _run(self, args: BaseModel) -> ToolExecutionResult:
        """Resolve the connection, gate writes, then dispatch.

        Returns:
            The tool result, or an approval-parking result.

        Raises:
            ForgeToolArgumentError: When the bound connection has an
                invalid base_url. Connection / credential / upstream
                failures propagate as other ``ForgeToolError`` leaves.
        """
        conn = await self._resolve_connection()
        if bool(getattr(args, "is_write", False)):
            parked = await self._gate.gate(
                _signature(self.name, self._runtime.connection_name, args),
                connection=self._runtime.connection_name,
                approval_id=None,
                title=f"Forge {self.name} on {self._runtime.connection_name!r}",
                description=f"Agent requests a forge {self.name} write.",
            )
            if parked is not None:
                return parked
        token = await self._resolve_token(conn)
        try:
            client = build_forge_agent_api_client(
                connection_type=conn.connection_type,
                base_url=str(conn.base_url),
                token=token,
                timeout=self._runtime.timeout_seconds,
            )
        except GitBackendConfigError as exc:
            raise ForgeToolArgumentError(safe_error_description(exc)) from exc
        try:
            return await self._dispatch_guarded(client, args)
        finally:
            await client.aclose()

    async def _resolve_connection(self) -> Connection:
        conn = await self._catalog.get(self._runtime.connection_name)
        if conn is None:
            msg = f"Forge connection {self._runtime.connection_name!r} not found"
            raise ForgeConnectionNotFoundError(msg)
        if not forge_agent_api_supported(conn.connection_type):
            msg = (
                f"Forge type {conn.connection_type.value!r} has no agent-operations"
                " client wired"
            )
            raise ForgeUnsupportedError(msg)
        if not conn.base_url:
            msg = f"Forge connection {conn.name!r} has no base_url"
            raise ForgeToolArgumentError(msg)
        return conn

    async def _resolve_token(self, conn: Connection) -> str:
        try:
            credentials = await self._catalog.get_credentials(conn.name)
        except SecretRetrievalError as exc:
            logger.warning(
                FORGE_TOOL_CREDENTIAL_FAILED,
                connection=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to broker credentials for forge connection"
            raise ForgeCredentialError(msg) from exc
        token = credentials.get("token")
        if not token:
            msg = f"Forge connection {conn.name!r} has no token"
            raise ForgeCredentialError(msg)
        return token

    async def _dispatch_guarded(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch and map lower-level forge-client errors to typed leaves.

        Returns:
            The tool result.

        Raises:
            ForgeRateLimitedError: The forge rate-limited the request.
            ForgeUpstreamError: An auth or other non-2xx / transport failure.
            ForgeUnsupportedError: The forge does not support the operation.
        """
        try:
            return await self._dispatch(client, args)
        except GitBackendRateLimitError as exc:
            msg = "Forge rate-limited the request; retry later"
            raise ForgeRateLimitedError(msg) from exc
        except GitBackendForgeAuthError as exc:
            msg = "Forge authentication failed (check the connection token/scopes)"
            raise ForgeUpstreamError(msg) from exc
        except GitBackendForgeApiError as exc:
            raise ForgeUpstreamError(safe_error_description(exc)) from exc
        except FeatureNotImplementedError as exc:
            raise ForgeUnsupportedError(str(exc)) from exc

    @abstractmethod
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Map the parsed action onto a client call and format the result."""

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Run the forge tool.

        Returns:
            The tool result (or an approval-parking result).
        """
        model = self.args_model
        assert model is not None  # noqa: S101 -- set by every subclass
        try:
            args = parse_typed("tool.execute", arguments, model)
        except PydanticValidationError as exc:
            return ToolExecutionResult(
                content=f"Invalid arguments: {safe_error_description(exc)}",
                is_error=True,
            )
        try:
            return await self._run(args)
        except ForgeToolError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)


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
    """Read CI runs for a repository (GitHub only)."""

    args_model: ClassVar[type[BaseModel] | None] = ForgeCiArgs

    def __init__(self, *, deps: ForgeToolDeps) -> None:
        super().__init__(
            name="forge_ci",
            description=(
                "Read continuous-integration runs for the bound forge repository:"
                " list runs (list_runs, optional branch) or get a single run"
                " (get_run, requires run_id). Available for GitHub connections."
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


def _signature(tool_name: str, connection: str, args: BaseModel) -> ActionSignature:
    return ActionSignature.build(
        namespace=tool_name,
        connection=connection,
        operation=str(getattr(args, "action", "")),
        payload=args.model_dump(mode="json"),
    )


def _json_result(data: object) -> ToolExecutionResult:
    return ToolExecutionResult(content=json.dumps(data, ensure_ascii=False))


__all__ = [
    "ForgeCiTool",
    "ForgeIssueTool",
    "ForgePullRequestTool",
    "ForgeRepoTool",
]
