"""Blocking sub-agent delegation tool.

``delegate_and_await`` lets a supervising agent offload a focused
sub-task to another agent, run it to completion inline, and receive the
child's answer plus a bounded transcript in the same turn. It is bound to
the supervisor's identity / task / project at invocation time (via
``_make_tool_invoker``) and live-gated per call on the
``engine.delegation_enabled`` setting, so an operator toggle applies
without a restart.
"""

import json
from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.delegation.errors import DelegationTargetNotFoundError
from synthorg.engine.delegation.models import DelegationSpec
from synthorg.engine.delegation.protocol import SubAgentRunner
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_DELEGATION_DISABLED,
    EXECUTION_DELEGATION_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.communication._args import DelegateAndAwaitArgs

logger = get_logger(__name__)

_ENGINE_NAMESPACE = "engine"
_ENABLED_KEY = "delegation_enabled"
_MAX_TURNS_KEY = "delegation_max_turns"


class DelegateAndAwaitTool(BaseTool):
    """Delegate a sub-task to another agent and await its transcript.

    Args:
        runner: The sub-agent runner that creates the child task and runs
            it to completion on the shared engine.
        config_resolver: Settings resolver, read per call for the live
            ``delegation_enabled`` flag and ``delegation_max_turns`` cap.
        requested_by: The supervising agent id (child-task creator).
        parent_task_id: The supervising task id (child ``parent_task_id``).
        project: Project scope the child task inherits.
    """

    args_model: ClassVar[type[BaseModel] | None] = DelegateAndAwaitArgs

    def __init__(
        self,
        *,
        runner: SubAgentRunner,
        config_resolver: ConfigResolver,
        requested_by: str,
        parent_task_id: str,
        project: str,
    ) -> None:
        super().__init__(
            name="delegate_and_await",
            description=(
                "Delegate a focused sub-task to another agent, run it to "
                "completion, and receive its final answer plus a transcript "
                "summary. Blocks until the child agent finishes."
            ),
            category=ToolCategory.COMMUNICATION,
            parameters_schema=DelegateAndAwaitArgs.model_json_schema(),
        )
        self._runner = runner
        self._config_resolver = config_resolver
        self._requested_by = requested_by
        self._parent_task_id = parent_task_id
        self._project = project

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run the delegated sub-task and fold the outcome back in.

        Returns:
            A ``ToolExecutionResult`` carrying the child's answer and a
            bounded transcript on success; an error result when
            delegation is disabled, the target is unknown, or the run
            raises.
        """
        args = parse_typed("tool.execute", arguments, DelegateAndAwaitArgs)
        if not await self._config_resolver.get_bool(_ENGINE_NAMESPACE, _ENABLED_KEY):
            logger.info(
                EXECUTION_DELEGATION_DISABLED,
                parent_task_id=self._parent_task_id,
                requested_by=self._requested_by,
            )
            return ToolExecutionResult(
                content="Delegation is disabled by operator configuration.",
                is_error=True,
            )
        max_turns = await self._config_resolver.get_int(
            _ENGINE_NAMESPACE,
            _MAX_TURNS_KEY,
        )
        spec = DelegationSpec(
            target=args.agent_id,
            title=args.title,
            description=args.description,
            project=self._project,
            parent_task_id=self._parent_task_id,
            requested_by=self._requested_by,
        )
        try:
            result = await self._runner.run(spec, max_turns=max_turns)
        except DelegationTargetNotFoundError as exc:
            logger.warning(
                EXECUTION_DELEGATION_FAILED,
                parent_task_id=self._parent_task_id,
                target=args.agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"No agent matches '{args.agent_id}'.",
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EXECUTION_DELEGATION_FAILED,
                parent_task_id=self._parent_task_id,
                target=args.agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Delegation failed: {safe_error_description(exc)}",
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps(
                {
                    "child_task_id": result.child_task_id,
                    "target_agent_id": result.target_agent_id,
                    "termination_reason": result.termination_reason.value,
                    "is_success": result.is_success,
                    "final_answer": result.final_answer,
                    "transcript_summary": result.transcript_summary,
                    "total_turns": result.total_turns,
                    "total_cost": result.total_cost,
                    "currency": result.currency,
                },
            ),
        )
