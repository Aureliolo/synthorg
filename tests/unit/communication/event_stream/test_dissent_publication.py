"""Tests for DissentRecord as first-class message type."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.enums import (
    ConflictResolutionStrategy,
    ConflictType,
    MessageType,
)
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import AgUiEventType
from synthorg.core.enums import SeniorityLevel


@pytest.mark.unit
class TestMessageTypeDissent:
    def test_dissent_exists(self) -> None:
        assert MessageType.DISSENT.value == "dissent"

    def test_dissent_is_string_enum(self) -> None:
        assert isinstance(MessageType.DISSENT, str)


@pytest.mark.unit
class TestDissentEventPublication:
    """Verify that dissent records can be projected to SSE events."""

    def _make_dissent_record(self) -> DissentRecord:
        ts = datetime(2026, 4, 13, tzinfo=UTC)
        positions = (
            ConflictPosition(
                agent_id="agent-001",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="Use microservices",
                reasoning="Better scaling",
                timestamp=ts,
            ),
            ConflictPosition(
                agent_id="agent-002",
                agent_department="engineering",
                agent_level=SeniorityLevel.MID,
                position="Use monolith",
                reasoning="Simpler deployment",
                timestamp=ts,
            ),
        )
        conflict = Conflict(
            id="conflict-abc123",
            type=ConflictType.ARCHITECTURE,
            subject="Service architecture choice",
            positions=positions,
            detected_at=ts,
        )
        resolution = ConflictResolution(
            conflict_id="conflict-abc123",
            outcome=ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY,
            winning_agent_id="agent-001",
            winning_position="Use microservices",
            decided_by="agent-001",
            reasoning="Senior decision",
            resolved_at=ts,
        )
        return DissentRecord(
            id="dissent-001",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id="agent-002",
            dissenting_position="Use monolith",
            strategy_used=ConflictResolutionStrategy.AUTHORITY,
            timestamp=ts,
        )

    async def test_dissent_event_via_hub(self) -> None:
        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")

        record = self._make_dissent_record()

        await hub.publish_raw(
            session_id="session-abc",
            event_type=AgUiEventType.DISSENT,
            agent_id=record.dissenting_agent_id,
            payload={
                "dissent_id": record.id,
                "conflict_id": record.conflict.id,
                "dissenting_agent_id": record.dissenting_agent_id,
            },
        )

        event = queue.get_nowait()
        assert event.type == AgUiEventType.DISSENT
        assert event.payload["dissent_id"] == "dissent-001"
        assert event.payload["conflict_id"] == "conflict-abc123"
