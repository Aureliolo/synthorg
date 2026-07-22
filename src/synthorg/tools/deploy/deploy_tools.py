"""Governed deploy tools: one destructive release, one read-only observer.

The split is deliberate. Triggering a release replaces what is running
and carries the full destructive treatment (guardrail triple, approval
parking, production action type). Reading a deployment's state or its
logs observes an outcome rather than causing one, so it stays a read: an
agent that could not cheaply poll the release it just made would be
unable to react to a failure, which is worse for safety, not better.
"""

from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.integrations.deploy_api import DeployApiClient
from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.observability import get_logger
from synthorg.observability.events.tool import DEPLOY_TOOL_RELEASE_REQUESTED
from synthorg.tools._governed_connection_tool import json_result
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.deploy._args import DeployReleaseArgs, DeployRunArgs
from synthorg.tools.deploy._base import _BaseDeployTool
from synthorg.tools.deploy._runtime import DeployToolDeps
from synthorg.tools.deploy.errors import DeployToolArgumentError

logger = get_logger(__name__)


class DeployReleaseTool(_BaseDeployTool):
    """Trigger a release to an allowlisted deploy target.

    Destructive: a release replaces what is currently serving. Every call
    carries the confirm + reason + actor guardrail and parks a human
    approval unless autonomy explicitly auto-approves this exact action
    type for this environment.
    """

    args_model: ClassVar[type[BaseModel] | None] = DeployReleaseArgs
    _DESTRUCTIVE: ClassVar[bool] = True

    def __init__(self, *, deps: DeployToolDeps, actor: AgentIdentity | None) -> None:
        super().__init__(
            name="deploy_release",
            description=(
                "Trigger a release to an allowlisted deploy target. Replaces "
                "what is running; requires confirm, a reason, and approval."
            ),
            args_model=DeployReleaseArgs,
            deps=deps,
        )
        self._actor = actor

    @override
    def _check_preconditions(self, args: BaseModel) -> None:
        """Enforce the confirm + reason + actor triple before the gate.

        Runs ahead of approval parking so an unconfirmed or unattributable
        release is refused outright, and the stated intent is recorded
        alongside the request the human will be asked to adjudicate.

        Args:
            args: The parsed release arguments.

        Raises:
            GuardrailViolationError: Confirm, reason, or actor missing.
            DeployToolArgumentError: Arguments were not the release shape.
        """
        reason, actor = require_admin_guardrails(
            args.model_dump(mode="json"), self._actor
        )
        if not isinstance(args, DeployReleaseArgs):
            msg = "deploy_release received unexpected arguments"
            raise DeployToolArgumentError(msg)
        logger.info(
            DEPLOY_TOOL_RELEASE_REQUESTED,
            connection=str(args.target),
            actor_id=str(actor.id),
            reason=reason,
        )

    @override
    async def _dispatch(
        self, client: DeployApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Trigger the release.

        Args:
            client: The platform client, pinned to the target.
            args: The parsed release arguments.

        Returns:
            The created deployment record.

        Raises:
            DeployToolArgumentError: Arguments were not the release shape.
        """
        if not isinstance(args, DeployReleaseArgs):
            msg = "deploy_release received unexpected arguments"
            raise DeployToolArgumentError(msg)
        deployment = await client.trigger_deployment(git_ref=args.git_ref)
        return json_result(deployment.model_dump(mode="json"))


class DeployRunTool(_BaseDeployTool):
    """Observe deployments on an allowlisted target (read-only)."""

    args_model: ClassVar[type[BaseModel] | None] = DeployRunArgs

    def __init__(self, *, deps: DeployToolDeps) -> None:
        super().__init__(
            name="deploy_run",
            description=(
                "Read a deployment's state, list recent deployments, or fetch "
                "a deployment's logs from an allowlisted deploy target."
            ),
            args_model=DeployRunArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: DeployApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Map the read action onto a client call.

        Args:
            client: The platform client, pinned to the target.
            args: The parsed read arguments.

        Returns:
            The deployment, deployment list, or log lines.

        Raises:
            DeployToolArgumentError: Arguments were not the read shape.
            DeploySetupRequiredError: The target declares no project.
        """
        if not isinstance(args, DeployRunArgs):
            msg = "deploy_run received unexpected arguments"
            raise DeployToolArgumentError(msg)
        if args.action == "get":
            deployment = await client.get_deployment(
                deployment_id=NotBlankStr(args.deployment_id)
            )
            return json_result(deployment.model_dump(mode="json"))
        if args.action == "logs":
            return await self._logs(client, args)
        deployments = await client.list_deployments(limit=args.limit)
        return json_result([d.model_dump(mode="json") for d in deployments])

    async def _logs(
        self, client: DeployApiClient, args: DeployRunArgs
    ) -> ToolExecutionResult:
        """Fetch a deployment's log lines, truncated to the runtime budget.

        Build logs routinely echo environment detail, so the volume an
        agent can pull in one call is bounded by the operator-set
        ``max_log_chars`` rather than by the platform's page size.

        Args:
            client: The platform client.
            args: The parsed read arguments.

        Returns:
            The log lines, truncated when over budget.
        """
        lines = await client.get_deployment_logs(
            deployment_id=NotBlankStr(args.deployment_id), limit=args.log_lines
        )
        budget = self._runtime.max_log_chars
        kept: list[dict[str, object]] = []
        used = 0
        for line in lines:
            used += len(line.text)
            if used > budget:
                break
            kept.append(line.model_dump(mode="json"))
        return json_result({"lines": kept, "truncated": len(kept) < len(lines)})


__all__ = ["DeployReleaseTool", "DeployRunTool"]
