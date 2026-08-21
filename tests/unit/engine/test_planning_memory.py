# module-kind: tests
"""Tests for memory-aware planning: the recall tool grant and the brief digest."""

from typing import TYPE_CHECKING, cast, override
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

if TYPE_CHECKING:
    from synthorg.api.state import AppState

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
    AgentSessionDecompositionStrategy,
)
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.planning_tool_provider import PlanningToolProvider
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.recall_request import MemoryRecallRequest
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolDefinition
from synthorg.security.autonomy.enums import ActionType
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.web.web_search import WebSearchProvider
from synthorg.workers._planning_memory import (
    PlanningMemoryGrant,
    build_planning_memory,
)
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

    @override
    def plans_any_task(self) -> bool:
        return True


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


class TestBuildPlanningMemory:
    """The settings-driven factory that gates the whole planning-memory grant."""

    @staticmethod
    def _patch(
        monkeypatch: pytest.MonkeyPatch,
        *,
        enabled: bool,
        digest_budget: int,
        memory_backend: MemoryBackend | None,
        org_backend: object | None,
    ) -> None:
        import synthorg.memory.state as memory_state
        import synthorg.workers._planning_memory as planning_mod

        resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=enabled),
            get_int=AsyncMock(return_value=digest_budget),
        )
        monkeypatch.setattr(planning_mod, "config_resolver_of", lambda _state: resolver)
        monkeypatch.setattr(
            memory_state, "memory_backend_or_none", lambda _state: memory_backend
        )
        monkeypatch.setattr(
            memory_state, "org_memory_backend_of", lambda _state: org_backend
        )

    async def test_disabled_returns_a_fully_off_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(
            monkeypatch,
            enabled=False,
            digest_budget=1000,
            memory_backend=mock_of[MemoryBackend](),
            org_backend=mock_of[OrgMemoryBackend](),
        )
        grant = await build_planning_memory(cast("AppState", object()))

        assert grant == PlanningMemoryGrant(None, 0, None, None)

    async def test_no_memory_backend_returns_off_even_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(
            monkeypatch,
            enabled=True,
            digest_budget=1000,
            memory_backend=None,
            org_backend=mock_of[OrgMemoryBackend](),
        )
        grant = await build_planning_memory(cast("AppState", object()))

        assert grant == PlanningMemoryGrant(None, 0, None, None)

    async def test_zero_budget_grants_the_tool_but_no_digest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = mock_of[MemoryBackend]()
        org = mock_of[OrgMemoryBackend]()
        self._patch(
            monkeypatch,
            enabled=True,
            digest_budget=0,
            memory_backend=backend,
            org_backend=org,
        )
        grant = await build_planning_memory(cast("AppState", object()))

        # The recall tool still gets its backends, but no digest is pre-seeded.
        assert grant.planning_memory is None
        assert grant.digest_budget == 0
        assert grant.memory_backend is backend
        assert grant.org_backend is org

    async def test_positive_budget_seeds_a_digest_strategy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = mock_of[MemoryBackend]()
        org = mock_of[OrgMemoryBackend]()
        self._patch(
            monkeypatch,
            enabled=True,
            digest_budget=1500,
            memory_backend=backend,
            org_backend=org,
        )
        grant = await build_planning_memory(cast("AppState", object()))

        assert grant.planning_memory is not None
        assert grant.digest_budget == 1500
        assert grant.memory_backend is backend
        assert grant.org_backend is org

    async def test_positive_budget_without_org_still_seeds_a_digest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = mock_of[MemoryBackend]()
        self._patch(
            monkeypatch,
            enabled=True,
            digest_budget=1500,
            memory_backend=backend,
            org_backend=None,
        )
        grant = await build_planning_memory(cast("AppState", object()))

        assert grant.planning_memory is not None
        assert grant.org_backend is None
