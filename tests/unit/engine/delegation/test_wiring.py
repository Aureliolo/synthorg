"""Wiring tests for the delegate tool and the engine's runner build."""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.engine._agent_tool_registry import registry_with_delegate_tool
from synthorg.engine.delegation.models import (
    SubAgentDelegationResult,
    SubAgentDelegationSpec,
)
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.registry import ToolRegistry
from tests._shared import mock_of
from tests._shared.ids import as_uuid

pytestmark = pytest.mark.unit

_DELEGATE_TOOL = "delegate_and_await"


class _NoopRunner:
    """Structural ``SubAgentRunner`` that never runs (wiring-only)."""

    async def run(
        self,
        spec: SubAgentDelegationSpec,
        *,
        max_turns: int,
        max_depth: int = 5,
        timeout_seconds: float | None = None,
    ) -> SubAgentDelegationResult:
        """Return a trivial result (unused by wiring tests)."""
        del spec, max_turns, max_depth, timeout_seconds
        return SubAgentDelegationResult(
            child_task_id="c",
            child_execution_id="e",
            target_agent_id="a",
            termination_reason=TerminationReason.COMPLETED,
            transcript_summary="",
            total_cost=0.0,
            currency=DEFAULT_CURRENCY,
            total_turns=0,
        )


def _identity() -> AgentIdentity:
    """Build a supervisor identity."""
    return AgentIdentity(
        id=as_uuid("supervisor"),
        name="Supervisor",
        role="Lead",
        department="Engineering",
        hiring_date=date(2026, 1, 1),
        personality=PersonalityConfig(traits=("decisive",)),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        tools=ToolPermissions(),
    )


def _resolver() -> ConfigResolver:
    return cast(
        ConfigResolver,
        mock_of[ConfigResolver](
            get_bool=AsyncMock(spec=ConfigResolver.get_bool, return_value=True),
            get_int=AsyncMock(spec=ConfigResolver.get_int, return_value=5),
        ),
    )


class TestRegistryWithDelegateTool:
    def test_adds_tool_when_fully_wired(self) -> None:
        registry = registry_with_delegate_tool(
            ToolRegistry([]),
            _NoopRunner(),
            _resolver(),
            _identity(),
            task_id="task-1",
            project_id="proj-1",
        )
        assert _DELEGATE_TOOL in registry

    def test_omits_tool_without_runner(self) -> None:
        registry = registry_with_delegate_tool(
            ToolRegistry([]),
            None,
            _resolver(),
            _identity(),
            task_id="task-1",
            project_id="proj-1",
        )
        assert _DELEGATE_TOOL not in registry

    def test_omits_tool_without_resolver(self) -> None:
        registry = registry_with_delegate_tool(
            ToolRegistry([]),
            _NoopRunner(),
            None,
            _identity(),
            task_id="task-1",
            project_id="proj-1",
        )
        assert _DELEGATE_TOOL not in registry

    def test_omits_tool_without_project_scope(self) -> None:
        registry = registry_with_delegate_tool(
            ToolRegistry([]),
            _NoopRunner(),
            _resolver(),
            _identity(),
            task_id="task-1",
            project_id=None,
        )
        assert _DELEGATE_TOOL not in registry

    def test_omits_tool_without_task(self) -> None:
        registry = registry_with_delegate_tool(
            ToolRegistry([]),
            _NoopRunner(),
            _resolver(),
            _identity(),
            task_id=None,
            project_id="proj-1",
        )
        assert _DELEGATE_TOOL not in registry


class TestEngineRunnerBuild:
    def test_engine_builds_runner_when_registry_wired(self) -> None:
        from synthorg.engine.agent_engine import AgentEngine
        from tests._shared.scripted_provider import ScriptedProvider

        engine = AgentEngine(
            provider=ScriptedProvider([]),
            task_engine=cast(TaskEngine, mock_of[TaskEngine]()),
            agent_registry=cast(
                AgentRegistryProtocol,
                mock_of[AgentRegistryProtocol](
                    get=AsyncMock(spec=AgentRegistryProtocol.get, return_value=None),
                    get_by_name=AsyncMock(
                        spec=AgentRegistryProtocol.get_by_name, return_value=None
                    ),
                ),
            ),
        )
        assert engine._sub_agent_runner is not None

    def test_engine_omits_runner_without_agent_registry(self) -> None:
        from synthorg.engine.agent_engine import AgentEngine
        from tests._shared.scripted_provider import ScriptedProvider

        engine = AgentEngine(
            provider=ScriptedProvider([]),
            task_engine=cast(TaskEngine, mock_of[TaskEngine]()),
        )
        assert engine._sub_agent_runner is None
