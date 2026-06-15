"""MultiAgentCoordinator enforces the max_delegation_rounds ceiling."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import DelegationRoundLimitError
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.service import TaskRoutingService
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task(chain: tuple[str, ...]) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="test-project",
        created_by="test-creator",
        delegation_chain=tuple(NotBlankStr(c) for c in chain),
    )


def _coordinator() -> MultiAgentCoordinator:
    return MultiAgentCoordinator(
        decomposition_service=mock_of[DecompositionService](),
        routing_service=mock_of[TaskRoutingService](),
        parallel_executor=mock_of[ParallelExecutorProtocol](),
    )


def _context(chain: tuple[str, ...], max_rounds: int = 3) -> CoordinationContext:
    return CoordinationContext(
        task=_task(chain),
        available_agents=(_identity(),),
        config=CoordinationConfig(max_delegation_rounds=max_rounds),
    )


class TestDelegationRoundsGuard:
    def test_below_soft_limit_passes(self) -> None:
        coordinator = _coordinator()
        coordinator._enforce_delegation_rounds(_context(("a", "b"), max_rounds=3))

    def test_at_soft_limit_warns_but_passes(self) -> None:
        coordinator = _coordinator()
        # Soft cap reached (3 hops, cap 3): warns, does not raise.
        coordinator._enforce_delegation_rounds(_context(("a", "b", "c"), max_rounds=3))

    def test_at_hard_limit_raises(self) -> None:
        coordinator = _coordinator()
        chain = ("a", "b", "c", "d", "e", "f")  # 6 hops == 2 * cap(3)
        with pytest.raises(DelegationRoundLimitError):
            coordinator._enforce_delegation_rounds(_context(chain, max_rounds=3))

    def test_hard_limit_carries_round_and_soft_cap(self) -> None:
        coordinator = _coordinator()
        chain = ("a", "b", "c", "d")  # 4 hops == 2 * cap(2)
        with pytest.raises(DelegationRoundLimitError) as exc_info:
            coordinator._enforce_delegation_rounds(_context(chain, max_rounds=2))
        assert exc_info.value.current_round == 4
        assert exc_info.value.soft_limit == 2
