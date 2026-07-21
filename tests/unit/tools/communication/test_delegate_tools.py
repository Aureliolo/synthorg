"""Unit tests for :class:`DelegateAndAwaitTool`."""

import json
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.engine.delegation.errors import DelegationTargetNotFoundError
from synthorg.engine.delegation.models import DelegationResult, DelegationSpec
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.communication.delegate_tools import DelegateAndAwaitTool
from tests._shared import mock_of

_PARENT_AGENT = "parent-agent"
_PARENT_TASK = "parent-task"
_PROJECT = "proj-001"


def _result(
    *,
    is_success: bool = True,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
) -> DelegationResult:
    """Build a delegation result for the fake runner to return."""
    return DelegationResult(
        child_task_id="child-task-1",
        child_execution_id="exec-1",
        target_agent_id="child-agent-1",
        termination_reason=termination_reason,
        is_success=is_success,
        final_answer="Root cause found.",
        transcript_summary="assistant: Root cause found.",
        total_cost=0.01,
        currency=DEFAULT_CURRENCY,
        total_turns=3,
    )


class _RecordingRunner:
    """Structural ``SubAgentRunner`` recording the spec and turn cap."""

    def __init__(
        self,
        *,
        result: DelegationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[DelegationSpec, int]] = []

    async def run(
        self,
        spec: DelegationSpec,
        *,
        max_turns: int,
    ) -> DelegationResult:
        """Record the call, then raise or return the configured outcome."""
        self.calls.append((spec, max_turns))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _resolver(*, enabled: bool = True, max_turns: int = 9) -> ConfigResolver:
    """Build a config resolver double for the delegation settings."""
    return cast(
        ConfigResolver,
        mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=enabled),
            get_int=AsyncMock(return_value=max_turns),
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


@pytest.mark.unit
class TestDelegateAndAwaitTool:
    async def test_runs_and_returns_child_outcome(self) -> None:
        runner = _RecordingRunner(result=_result())
        tool = _tool(runner, _resolver(max_turns=9))

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["child_task_id"] == "child-task-1"
        assert payload["is_success"] is True
        assert payload["final_answer"] == "Root cause found."
        assert payload["total_turns"] == 3
        # The supervisor context is bound onto the spec, not taken from args.
        spec, max_turns = runner.calls[0]
        assert spec.parent_task_id == _PARENT_TASK
        assert spec.requested_by == _PARENT_AGENT
        assert spec.project == _PROJECT
        assert spec.target == "child-agent-1"
        assert max_turns == 9

    async def test_disabled_refuses_without_running(self) -> None:
        runner = _RecordingRunner(result=_result())
        tool = _tool(runner, _resolver(enabled=False))

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "disabled" in result.content.lower()
        assert runner.calls == []

    async def test_unknown_target_is_error_result(self) -> None:
        runner = _RecordingRunner(
            error=DelegationTargetNotFoundError(target="child-agent-1"),
        )
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "child-agent-1" in result.content

    async def test_runner_failure_is_error_result(self) -> None:
        runner = _RecordingRunner(error=RuntimeError("boom"))
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        assert result.is_error is True
        assert "failed" in result.content.lower()

    async def test_failed_child_is_reported_not_errored(self) -> None:
        runner = _RecordingRunner(
            result=_result(
                is_success=False,
                termination_reason=TerminationReason.MAX_TURNS,
            ),
        )
        tool = _tool(runner, _resolver())

        result = await tool.execute(arguments=dict(_ARGS))

        # The delegation ran; the child's failure is data, not a tool error.
        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["is_success"] is False
        assert payload["termination_reason"] == "max_turns"
