# module-kind: tests
"""Tests for memory-aware planning: the recall tool grant and the brief digest."""

from typing import override

import pytest
from pydantic import JsonValue

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
    AgentSessionDecompositionStrategy,
)
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
)
from synthorg.engine.decomposition.planning_tool_provider import PlanningToolProvider
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.recall_request import MemoryRecallRequest
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolDefinition
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.web.web_search import WebSearchProvider
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_e2e_identity,
    make_text_response,
)

pytestmark = pytest.mark.unit

_DIGEST_MARKER = "ORG_PLAYBOOK_DIGEST_MARKER"


def _task() -> Task:
    return Task(
        id=as_uuid("obj-1"),
        title="Build a Tetris web game",
        description="A playable browser Tetris.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="tetris-web",
        created_by="ceo",
    )


def _plan_args() -> dict[str, JsonValue]:
    return {
        "subtasks": [
            {
                "id": "s1",
                "title": "Board renderer",
                "description": "Render the grid",
                "stakes": "normal",
                "required_role": "Frontend Engineer",
                "expected_artifacts": ["src/board.tsx"],
                "acceptance_criteria": ["grid renders"],
            },
        ],
        "task_structure": "sequential",
        "coordination_topology": "auto",
    }


class _SentinelFallback(DecompositionStrategy):
    @override
    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        del task, context
        msg = "fallback should not run"
        raise AssertionError(msg)

    @override
    def get_strategy_name(self) -> str:
        return "sentinel"


class _MarkerMemory:
    """A MemoryInjectionStrategy that records its request and injects a marker."""

    def __init__(self) -> None:
        self.seen: MemoryRecallRequest | None = None

    async def prepare_messages(
        self, request: MemoryRecallRequest
    ) -> tuple[ChatMessage, ...]:
        self.seen = request
        return (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"{_DIGEST_MARKER}: reviews need two approvals here.",
            ),
        )

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return ()

    @property
    def strategy_name(self) -> str:
        return "marker"


class TestPlanningToolProviderRecallGrant:
    def test_grants_a_read_only_memory_recall_tool(self) -> None:
        provider = PlanningToolProvider(
            search_provider=None,
            memory_backend=mock_of[MemoryBackend](),
        )

        tools = provider.build_tools(owner_id="owner-1", project_id="proj-1")

        assert len(tools) == 1
        # It carries the read-only memory action type, so it survives the
        # planning session's read-only tool filter.
        assert tools[0].action_type == ActionType.MEMORY_READ

    def test_grants_both_web_and_memory_when_both_wired(self) -> None:
        provider = PlanningToolProvider(
            search_provider=mock_of[WebSearchProvider](),
            memory_backend=mock_of[MemoryBackend](),
        )

        tools = provider.build_tools(owner_id="owner-1", project_id=None)

        assert len(tools) == 2

    def test_grants_nothing_without_backends(self) -> None:
        provider = PlanningToolProvider(search_provider=None)
        assert provider.build_tools(owner_id="owner-1", project_id=None) == ()


class TestPlanningDigestInjection:
    async def test_digest_reaches_the_planning_prompt(self) -> None:
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        memory = _MarkerMemory()
        strategy = AgentSessionDecompositionStrategy(
            provider_selector=lambda _identity: provider,
            fallback=_SentinelFallback(),
            config=AgentSessionDecompositionConfig(max_turns=4),
            planning_memory=memory,
        )
        context = DecompositionContext(owner_identity=make_e2e_identity())

        await strategy.decompose(_task(), context)

        # The digest was requested for the objective and reached the prompt.
        assert memory.seen is not None
        assert memory.seen.objective == "A playable browser Tetris."
        sent = "\n".join(
            m.content or ""
            for call_messages in provider.received_messages
            for m in call_messages
        )
        assert _DIGEST_MARKER in sent

    async def test_zero_budget_injects_no_digest(self) -> None:
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        memory = _MarkerMemory()
        strategy = AgentSessionDecompositionStrategy(
            provider_selector=lambda _identity: provider,
            fallback=_SentinelFallback(),
            config=AgentSessionDecompositionConfig(max_turns=4, memory_digest_budget=0),
            planning_memory=memory,
        )
        context = DecompositionContext(owner_identity=make_e2e_identity())

        await strategy.decompose(_task(), context)

        assert memory.seen is None
