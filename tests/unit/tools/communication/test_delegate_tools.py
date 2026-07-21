"""Unit tests for :class:`DelegateAndAwaitTool`."""

import json
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.engine.delegation.errors import (
    SubAgentDelegationDepthExceededError,
    SubAgentDelegationTargetNotFoundError,
)
from synthorg.engine.delegation.models import (
    SubAgentDelegationResult,
    SubAgentDelegationSpec,
)
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.security.autonomy.enums import ActionType
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.communication.delegate_tools import DelegateAndAwaitTool
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PARENT_AGENT = "parent-agent"
_PARENT_TASK = "parent-task"
_PROJECT = "proj-001"


def _result(
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
) -> SubAgentDelegationResult:
    """Build a delegation result for the fake runner to return."""
    return SubAgentDelegationResult(
        child_task_id="child-task-1",
        child_execution_id="exec-1",
        target_agent_id="child-agent-1",
        termination_reason=termination_reason,
        final_answer="Root cause found.",
        transcript_summary="assistant: Root cause found.",
        total_cost=0.01,
        currency=DEFAULT_CURRENCY,
        total_turns=3,
    )


class _RecordingRunner:
    """Structural ``SubAgentRunner`` recording the spec and caps."""

    def __init__(
        self,
        *,
        result: SubAgentDelegationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        spec: SubAgentDelegationSpec,
        *,
        max_turns: int,
        max_depth: int = 5,
        timeout_seconds: float | None = None,
    ) -> SubAgentDelegationResult:
        """Record the call, then raise or return the configured outcome."""
        self.calls.append(
            {
                "spec": spec,
                "max_turns": max_turns,
                "max_depth": max_depth,
                "timeout_seconds": timeout_seconds,
            },
        )
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _resolver(
    *,
    enabled: bool = True,
    max_turns: int = 9,
    max_depth: int = 4,
    timeout: float = 0.0,
) -> ConfigResolver:
    """Build a config resolver double for the delegation settings."""
    return cast(
        ConfigResolver,
        mock_of[ConfigResolver](
            get_bool=AsyncMock(spec=ConfigResolver.get_bool, return_value=enabled),
            get_int=AsyncMock(
                spec=ConfigResolver.get_int,
                side_effect=[max_turns, max_depth],
            ),
            get_float=AsyncMock(spec=ConfigResolver.get_float, return_value=timeout),
        ),
    )


def _tool(
    runner: _RecordingRunner,
    resolver: ConfigResolver,
) -> DelegateAndAwaitTool:
    """Build the tool bound to fixed supervisor context."""
    return DelegateAndAwaitTool(
        runner=runner,
        config_resolver=resolver,
        requested_by=_PARENT_AGENT,
        parent_task_id=_PARENT_TASK,
        project=_PROJECT,
    )


_ARGS = {
    "agent_id": "child-agent-1",
    "title": "Investigate",
    "description": "Find the root cause.",
}


class TestDelegateAndAwaitTool:
    def test_declares_org_delegate_action_type(self) -> None:
        tool = _tool(_RecordingRunner(result=_result()), _resolver())
        assert tool.action_type == ActionType.ORG_DELEGATE.value

    async def test_runs_and_returns_child_outcome(self) -> None:
        runner = _RecordingRunner(result=_result())
        tool = _tool(runner, _resolver(max_turns=9, max_depth=4, timeout=30.0))

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["child_task_id"] == "child-task-1"
        assert payload["is_success"] is True
        assert payload["final_answer"] == "Root cause found."
        assert payload["total_turns"] == 3
        # The supervisor context is bound onto the spec, not taken from args.
        call = runner.calls[0]
        spec = cast(SubAgentDelegationSpec, call["spec"])
        assert spec.parent_task_id == _PARENT_TASK
        assert spec.requested_by == _PARENT_AGENT
        assert spec.project == _PROJECT
        assert spec.target == "child-agent-1"
        assert call["max_turns"] == 9
        assert call["max_depth"] == 4
        assert call["timeout_seconds"] == 30.0

    async def test_zero_timeout_passed_as_none(self) -> None:
        runner = _RecordingRunner(result=_result())
        tool = _tool(runner, _resolver(timeout=0.0))

        await tool.execute(arguments=dict(_ARGS))

        assert runner.calls[0]["timeout_seconds"] is None

    async def test_disabled_refuses_without_running(self) -> None:
        runner = _RecordingRunner(result=_result())
        tool = _tool(runner, _resolver(enabled=False))

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "disabled" in result.content.lower()
        assert runner.calls == []

    async def test_unknown_target_is_error_result(self) -> None:
        runner = _RecordingRunner(
            error=SubAgentDelegationTargetNotFoundError(target="child-agent-1"),
        )
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "child-agent-1" in result.content

    async def test_depth_exceeded_is_error_result(self) -> None:
        runner = _RecordingRunner(
            error=SubAgentDelegationDepthExceededError(depth=5, max_depth=5),
        )
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "depth" in result.content.lower() or "cycle" in result.content.lower()

    async def test_runner_failure_is_error_result(self) -> None:
        runner = _RecordingRunner(error=RuntimeError("boom"))
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "failed" in result.content.lower()

    async def test_failed_child_is_reported_not_errored(self) -> None:
        runner = _RecordingRunner(
            result=_result(termination_reason=TerminationReason.MAX_TURNS),
        )
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        # The delegation ran; the child's failure is data, not a tool error.
        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["is_success"] is False
        assert payload["termination_reason"] == "max_turns"
