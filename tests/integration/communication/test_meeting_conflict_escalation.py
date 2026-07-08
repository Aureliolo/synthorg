"""Integration: meeting conflict -> conflict resolution -> escalation queue.

Exercises the wiring added for #2543 end to end with real components: the
meeting orchestrator invokes the escalation bridge, which builds a conflict
from participant positions and drives it through a real
``ConflictResolutionService``. The meeting protocol itself is stubbed (its
LLM-heavy flow is covered by the meeting unit suites); everything downstream
of the bridge is real.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    WinnerOnlyDecisionProcessor,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.factory import (
    build_conflict_resolution_service,
)
from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.protocol import JudgeDecision
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.conflict_escalation import (
    MeetingConflictEscalationBridge,
)
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from tests._shared.ids import as_uuid
from tests.unit.communication.meeting.conftest import make_mock_agent_caller

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_SUBJECT = "Adopt REST or gRPC"


class _FakeJudge:
    """A judge evaluator returning a fixed verdict."""

    def __init__(self, winner_id: str) -> None:
        self._winner_id = winner_id

    async def evaluate(
        self,
        conflict: object,
        judge_agent_id: NotBlankStr,
    ) -> JudgeDecision:
        del conflict, judge_agent_id
        return JudgeDecision(winning_agent_id=self._winner_id, reasoning="verdict")


async def _register(
    registry: AgentRegistryService,
    label: str,
    *,
    department: str,
    level: SeniorityLevel,
) -> str:
    identity = AgentIdentity(
        id=as_uuid(label),
        name=label,
        role="Developer",
        department=department,
        level=level,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        tools=ToolPermissions(),
    )
    await registry.register(identity)
    return str(identity.id)


def _minutes(participant_ids: tuple[str, str]) -> MeetingMinutes:
    contributions = tuple(
        MeetingContribution(
            agent_id=pid,
            content=f"{pid} defends position {index}",
            phase=MeetingPhase.DISCUSSION,
            turn_number=index + 1,
            timestamp=_NOW,
        )
        for index, pid in enumerate(participant_ids)
    )
    return MeetingMinutes(
        meeting_id="mtg-conflict",
        protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
        leader_id="leader",
        participant_ids=participant_ids,
        agenda=MeetingAgenda(title=_SUBJECT),
        contributions=contributions,
        conflicts_detected=True,
        started_at=_NOW,
        ended_at=_NOW,
    )


async def test_meeting_conflict_reaches_human_escalation_queue() -> None:
    store = InMemoryEscalationStore()
    # timeout_seconds=0 fires an immediate timeout after the row is created,
    # so the resolver never blocks waiting for an operator decision.
    human_resolver = HumanEscalationResolver(store=store, timeout_seconds=0)
    service = ConflictResolutionService(
        config=ConflictResolutionConfig(strategy=ConflictResolutionStrategy.HUMAN),
        resolvers={ConflictResolutionStrategy.HUMAN: human_resolver},
    )
    registry = AgentRegistryService()
    alice = await _register(
        registry, "alice", department="Engineering", level=SeniorityLevel.SENIOR
    )
    bob = await _register(
        registry, "bob", department="Platform", level=SeniorityLevel.MID
    )
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=registry,
    )
    minutes = _minutes((alice, bob))

    mock_protocol = MagicMock()
    mock_protocol.get_protocol_type.return_value = MeetingProtocolType.STRUCTURED_PHASES
    mock_protocol.run = AsyncMock(return_value=minutes)
    orchestrator = MeetingOrchestrator(
        protocol_registry={MeetingProtocolType.STRUCTURED_PHASES: mock_protocol},
        agent_caller=make_mock_agent_caller(),
        conflict_escalation_hook=bridge,
    )

    record = await orchestrator.run_meeting(
        meeting_type_name="design-review",
        protocol_config=MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES
        ),
        agenda=MeetingAgenda(title=_SUBJECT),
        leader_id="leader",
        participant_ids=(alice, bob),
        token_budget=10000,
    )

    assert record.status == MeetingStatus.COMPLETED
    # The conflict landed on the human escalation queue (create() was reached).
    escalations, total = await store.list_items(status=None)
    assert total == 1
    assert escalations[0].conflict.subject == _SUBJECT


async def test_hybrid_clear_winner_auto_resolves_without_escalation() -> None:
    from tests.unit.communication.conflict_resolution.conftest import make_company

    store = InMemoryEscalationStore()
    registry = AgentRegistryService()
    alice = await _register(
        registry, "alice", department="Engineering", level=SeniorityLevel.SENIOR
    )
    bob = await _register(
        registry, "bob", department="Platform", level=SeniorityLevel.MID
    )
    service = build_conflict_resolution_service(
        config=ConflictResolutionConfig(strategy=ConflictResolutionStrategy.HYBRID),
        company=make_company(),
        escalation_store=store,
        escalation_processor=WinnerOnlyDecisionProcessor(),
        escalation_registry=PendingFuturesRegistry(),
        judge_evaluator=_FakeJudge(winner_id=alice),
    )
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=registry,
    )

    await bridge(_minutes((alice, bob)))

    # A clear winner auto-resolves: nothing is escalated to the human queue.
    _escalations, total = await store.list_items(status=None)
    assert total == 0
    # A resolution was produced and the losing position was recorded as dissent.
    dissents = service.get_dissent_records()
    assert len(dissents) == 1
    assert dissents[0].dissenting_agent_id == bob
