"""Unit tests for the post-meeting conflict-escalation bridge."""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
)
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.enums import ConflictType
from synthorg.communication.errors import ConflictResolutionError
from synthorg.communication.meeting.conflict_escalation import (
    MeetingConflictEscalationBridge,
)
from synthorg.communication.meeting.enums import MeetingPhase, MeetingProtocolType
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of
from tests._shared.ids import as_uuid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)


def _identity(label: str, *, department: str, level: SeniorityLevel) -> AgentIdentity:
    return AgentIdentity(
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


def _contribution(
    agent_id: str,
    *,
    content: str,
    phase: MeetingPhase,
    turn: int,
) -> MeetingContribution:
    return MeetingContribution(
        agent_id=agent_id,
        content=content,
        phase=phase,
        turn_number=turn,
        timestamp=_NOW,
    )


def _minutes(
    *,
    conflicts_detected: bool = True,
    contributions: tuple[MeetingContribution, ...] = (),
    participant_ids: tuple[str, ...] = ("alice", "bob"),
) -> MeetingMinutes:
    return MeetingMinutes(
        meeting_id="meeting-1",
        protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
        leader_id="leader",
        participant_ids=participant_ids,
        agenda=MeetingAgenda(title="Adopt REST or gRPC"),
        contributions=contributions,
        conflicts_detected=conflicts_detected,
        started_at=_NOW,
        ended_at=_NOW,
    )


def _resolution() -> ConflictResolution:
    return ConflictResolution(
        conflict_id="c1",
        outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        decided_by="human",
        reasoning="escalated",
        resolved_at=_NOW,
    )


def _conflict() -> Conflict:
    positions = (
        ConflictPosition(
            agent_id="alice",
            agent_department="Engineering",
            agent_level=SeniorityLevel.SENIOR,
            position="REST",
            reasoning="REST is simpler",
            timestamp=_NOW,
        ),
        ConflictPosition(
            agent_id="bob",
            agent_department="Platform",
            agent_level=SeniorityLevel.MID,
            position="gRPC",
            reasoning="gRPC is faster",
            timestamp=_NOW,
        ),
    )
    return Conflict(
        id=as_uuid("c1"),
        type=ConflictType.OTHER,
        subject="Adopt REST or gRPC",
        positions=positions,
        detected_at=_NOW,
    )


def _service() -> Any:  # type: ignore[explicit-any]  # mock_of returns Any for mock-API ergonomics
    service = mock_of[ConflictResolutionService]()
    service.create_conflict.return_value = _conflict()
    service.resolve.return_value = (_resolution(), ())
    return service


def _registry(identities: dict[str, AgentIdentity]) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any
    registry = mock_of[AgentRegistryService]()
    registry.get_by_ids = AsyncMock(return_value=identities)
    return registry


def _both_identities() -> dict[str, AgentIdentity]:
    return {
        "alice": _identity(
            "alice", department="Engineering", level=SeniorityLevel.SENIOR
        ),
        "bob": _identity("bob", department="Platform", level=SeniorityLevel.MID),
    }


async def test_builds_conflict_from_discussion_contributions() -> None:
    service = _service()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )
    minutes = _minutes(
        contributions=(
            _contribution(
                "alice",
                content="REST is simpler.\nIt is well understood.",
                phase=MeetingPhase.DISCUSSION,
                turn=1,
            ),
            _contribution(
                "bob",
                content="gRPC is faster",
                phase=MeetingPhase.DISCUSSION,
                turn=2,
            ),
        ),
    )

    await bridge(minutes)

    positions = service.create_conflict.call_args.kwargs["positions"]
    assert {p.agent_id for p in positions} == {"alice", "bob"}
    alice = next(p for p in positions if p.agent_id == "alice")
    assert alice.agent_department == "Engineering"
    assert alice.agent_level is SeniorityLevel.SENIOR
    # Full text is the reasoning; the position is the capped first line.
    assert alice.reasoning == "REST is simpler.\nIt is well understood."
    assert alice.position == "REST is simpler."
    assert service.create_conflict.call_args.kwargs["task_id"] == "meeting-1"
    service.resolve.assert_awaited_once()


async def test_falls_back_to_input_gathering_when_no_discussion() -> None:
    service = _service()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )
    minutes = _minutes(
        contributions=(
            _contribution(
                "alice", content="REST", phase=MeetingPhase.DISCUSSION, turn=1
            ),
            _contribution(
                "bob",
                content="gRPC position paper",
                phase=MeetingPhase.INPUT_GATHERING,
                turn=0,
            ),
        ),
    )

    await bridge(minutes)

    positions = service.create_conflict.call_args.kwargs["positions"]
    bob = next(p for p in positions if p.agent_id == "bob")
    assert bob.reasoning == "gRPC position paper"


async def test_no_op_when_conflicts_not_detected() -> None:
    service = _service()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )

    await bridge(_minutes(conflicts_detected=False))

    service.create_conflict.assert_not_called()
    service.resolve.assert_not_awaited()


async def test_no_op_when_kill_switch_disabled() -> None:
    service = _service()
    resolver = mock_of[ConfigResolverProtocol]()
    resolver.get_bool = AsyncMock(return_value=False)
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
        config_resolver=resolver,
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="b", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    await bridge(minutes)

    service.create_conflict.assert_not_called()


async def test_resolver_error_is_swallowed() -> None:
    service = _service()
    service.resolve = AsyncMock(side_effect=ConflictResolutionError("boom"))
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="b", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    # Must not raise: the meeting already completed.
    await bridge(minutes)


async def test_critical_exception_propagates() -> None:
    service = _service()
    service.resolve = AsyncMock(side_effect=MemoryError())
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="b", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    with pytest.raises(MemoryError):
        await bridge(minutes)


async def test_skips_when_fewer_than_two_resolvable_agents() -> None:
    service = _service()
    # Only alice resolves in the registry; bob is missing.
    registry = _registry(
        {"alice": _identity("alice", department="Eng", level=SeniorityLevel.SENIOR)}
    )
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=registry,
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="b", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    await bridge(minutes)

    service.create_conflict.assert_not_called()


async def test_blank_content_contributions_filtered() -> None:
    service = _service()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry(_both_identities()),
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="   ", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    await bridge(minutes)

    service.create_conflict.assert_not_called()


async def test_skips_when_all_agents_missing_from_registry() -> None:
    service = _service()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=service,
        agent_registry=_registry({}),
    )
    minutes = _minutes(
        contributions=(
            _contribution("alice", content="a", phase=MeetingPhase.DISCUSSION, turn=1),
            _contribution("bob", content="b", phase=MeetingPhase.DISCUSSION, turn=2),
        ),
    )

    await bridge(minutes)

    service.create_conflict.assert_not_called()
