"""Blocking sub-agent delegation tool.

``delegate_and_await`` lets a supervising agent offload a focused
sub-task to another agent, run it to completion inline, and receive the
child's answer plus a bounded transcript in the same turn. It is bound to
the supervisor's identity / task / project at invocation time (via
``_make_tool_invoker``) and live-gated per call on the
``engine.delegation_enabled`` setting, so an operator toggle applies
without a restart. Spawning a full budgeted, tool-capable child run is a
materially higher-risk action than an ordinary internal message, so the
tool declares an explicit ``org:delegate`` action type (classified HIGH
in the default risk map) rather than inheriting the LOW ``comms:internal``
default of its category.
"""

import json
from dataclasses import dataclass
from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.delegation.errors import (
    SubAgentDelegationDepthExceededError,
    SubAgentDelegationTargetNotFoundError,
)
from synthorg.engine.delegation.models import (
    SubAgentDelegationResult,
    SubAgentDelegationSpec,
)
from synthorg.engine.delegation.protocol import SubAgentRunner
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_DELEGATION_DISABLED,
    EXECUTION_DELEGATION_FAILED,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.settings.kill_switch import (
    resolve_bool_with_fallback,
    resolve_float_with_fallback,
    resolve_int_with_fallback,
)
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.communication._args import DelegateAndAwaitArgs

logger = get_logger(__name__)

_ENGINE_NAMESPACE = "engine"
_ENABLED_KEY = "delegation_enabled"
_MAX_TURNS_KEY = "delegation_max_turns"
_MAX_DEPTH_KEY = "delegation_max_depth"
_TIMEOUT_KEY = "delegation_timeout_seconds"

# Fallbacks MUST match the registered SettingDefinition defaults so the
# resolver-up and resolver-down (outage) paths agree.
_DEFAULT_ENABLED: Final[bool] = True
_DEFAULT_MAX_TURNS: Final[int] = 10
_DEFAULT_MAX_DEPTH: Final[int] = 5
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 0.0


@dataclass(frozen=True, slots=True)
class _DelegationLimits:
    """Resolved turn / depth / timeout caps for one delegation call."""

    max_turns: int
    max_depth: int
    timeout_seconds: float | None


class DelegateAndAwaitTool(BaseTool):
    """Delegate a sub-task to another agent and await its transcript.

    Args:
        runner: The sub-agent runner that creates the child task and runs
            it to completion on the shared engine.
        config_resolver: Settings resolver, read per call for the live
            ``delegation_enabled`` flag and the turn / depth / timeout caps.
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
            action_type=ActionType.ORG_DELEGATE.value,
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
            delegation is disabled, the target is unknown, the chain is
            too deep / cyclic, or the run raises.
        """
        args = parse_typed("tool.execute", arguments, DelegateAndAwaitArgs)
        if not await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_ENGINE_NAMESPACE,
            key=_ENABLED_KEY,
            fallback=_DEFAULT_ENABLED,
        ):
            logger.info(
                EXECUTION_DELEGATION_DISABLED,
                parent_task_id=self._parent_task_id,
                requested_by=self._requested_by,
            )
            return ToolExecutionResult(
                content="Delegation is disabled by operator configuration.",
                is_error=True,
            )
        limits = await self._resolve_limits()
        spec = SubAgentDelegationSpec(
            target=args.agent_id,
            title=args.title,
            description=args.description,
            project=self._project,
            parent_task_id=self._parent_task_id,
            requested_by=self._requested_by,
        )
        outcome = await self._run_delegation(spec, limits)
        if isinstance(outcome, ToolExecutionResult):
            return outcome
        return self._success_result(outcome)

    async def _resolve_limits(self) -> _DelegationLimits:
        """Resolve the live turn / depth / timeout caps from settings.

        Returns:
            The resolved :class:`_DelegationLimits` for this call.
        """
        max_turns = await resolve_int_with_fallback(
            resolver=self._config_resolver,
            namespace=_ENGINE_NAMESPACE,
            key=_MAX_TURNS_KEY,
            fallback=_DEFAULT_MAX_TURNS,
        )
        max_depth = await resolve_int_with_fallback(
            resolver=self._config_resolver,
            namespace=_ENGINE_NAMESPACE,
            key=_MAX_DEPTH_KEY,
            fallback=_DEFAULT_MAX_DEPTH,
        )
        timeout = await resolve_float_with_fallback(
            resolver=self._config_resolver,
            namespace=_ENGINE_NAMESPACE,
            key=_TIMEOUT_KEY,
            fallback=_DEFAULT_TIMEOUT_SECONDS,
        )
        return _DelegationLimits(
            max_turns=max_turns,
            max_depth=max_depth,
            timeout_seconds=timeout if timeout > 0 else None,
        )

    async def _run_delegation(
        self,
        spec: SubAgentDelegationSpec,
        limits: _DelegationLimits,
    ) -> SubAgentDelegationResult | ToolExecutionResult:
        """Run the child agent, mapping delegation failures to an error result.

        Returns:
            The :class:`SubAgentDelegationResult` on success, or an
            ``is_error`` :class:`ToolExecutionResult` when the target is
            unknown, the chain is too deep / cyclic, or the run raises.
        """
        try:
            return await self._runner.run(
                spec,
                max_turns=limits.max_turns,
                max_depth=limits.max_depth,
                timeout_seconds=limits.timeout_seconds,
            )
        except SubAgentDelegationTargetNotFoundError as exc:
            return self._error_result(
                spec.target,
                exc,
                content=f"No agent matches '{spec.target}'.",
            )
        except SubAgentDelegationDepthExceededError as exc:
            return self._error_result(
                spec.target,
                exc,
                content=(
                    "Delegation refused: the delegation chain is at its depth "
                    "limit or would form a cycle."
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return self._error_result(
                spec.target,
                exc,
                content=f"Delegation failed: {safe_error_description(exc)}",
            )

    def _success_result(self, result: SubAgentDelegationResult) -> ToolExecutionResult:
        """Fold a completed delegation into the tool's success result.

        Returns:
            A ``ToolExecutionResult`` carrying the child's answer and a
            bounded transcript summary as JSON content.
        """
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

    def _error_result(
        self,
        target: str,
        exc: Exception,
        *,
        content: str,
    ) -> ToolExecutionResult:
        """Log the delegation failure and build the error tool result.

        Returns:
            An ``is_error`` :class:`ToolExecutionResult` carrying *content*.
        """
        logger.warning(
            EXECUTION_DELEGATION_FAILED,
            parent_task_id=self._parent_task_id,
            target=target,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ToolExecutionResult(content=content, is_error=True)
