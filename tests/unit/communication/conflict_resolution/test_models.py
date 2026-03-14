"""Tests for conflict resolution domain models."""

import pytest
from pydantic import ValidationError

from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.enums import (
    ConflictResolutionStrategy,
    ConflictType,
)
from synthorg.core.enums import SeniorityLevel

from .conftest import make_conflict, make_position, make_resolution

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestConflictPosition:
    def test_valid_position(self) -> None:
        pos = make_position()
        assert pos.agent_id == "agent-a"
        assert pos.agent_level == SeniorityLevel.SENIOR

    def test_frozen(self) -> None:
        pos = make_position()
        with pytest.raises(ValidationError):
            pos.agent_id = "changed"  # type: ignore[misc]

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id"):
            make_position(agent_id="   ")

    def test_blank_position_rejected(self) -> None:
        with pytest.raises(ValidationError, match="position"):
            make_position(position="  ")


@pytest.mark.unit
class TestConflict:
    def test_valid_conflict(self) -> None:
        conflict = make_conflict()
        assert conflict.id == "conflict-test12345"
        assert conflict.type == ConflictType.ARCHITECTURE
        assert len(conflict.positions) == 2

    def test_frozen(self) -> None:
        conflict = make_conflict()
        with pytest.raises(ValidationError):
            conflict.subject = "changed"  # type: ignore[misc]

    def test_minimum_two_positions_required(self) -> None:
        with pytest.raises(ValidationError, match="at least 2"):
            make_conflict(positions=(make_position(),))

    def test_duplicate_agent_ids_rejected(self) -> None:
        pos_a = make_position(agent_id="same-agent")
        pos_b = make_position(agent_id="same-agent", position="Other view")
        with pytest.raises(ValidationError, match="Duplicate agent_id"):
            make_conflict(positions=(pos_a, pos_b))

    def test_optional_task_id(self) -> None:
        conflict = make_conflict(task_id="task-123")
        assert conflict.task_id == "task-123"

    def test_cross_department_computed(self) -> None:
        conflict = make_conflict(
            positions=(
                make_position(agent_id="agent-a", department="engineering"),
                make_position(
                    agent_id="agent-b",
                    department="qa",
                    position="Other view",
                ),
            ),
        )
        assert conflict.is_cross_department is True

    def test_same_department_not_cross(self) -> None:
        conflict = make_conflict()
        assert conflict.is_cross_department is False

    def test_three_positions_valid(self) -> None:
        pos_c = make_position(
            agent_id="agent-c",
            position="Use serverless",
            reasoning="Cost effective",
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="agent-a"),
                make_position(
                    agent_id="agent-b",
                    position="Use monolith",
                    reasoning="Simple",
                ),
                pos_c,
            ),
        )
        assert len(conflict.positions) == 3


@pytest.mark.unit
class TestConflictResolution:
    def test_resolved_by_authority(self) -> None:
        res = make_resolution()
        assert res.outcome == ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY
        assert res.winning_agent_id == "agent-a"

    def test_frozen(self) -> None:
        res = make_resolution()
        with pytest.raises(ValidationError):
            res.outcome = ConflictResolutionOutcome.RESOLVED_BY_DEBATE  # type: ignore[misc]

    def test_escalated_requires_no_winner(self) -> None:
        res = make_resolution(
            outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by="human",
        )
        assert res.winning_agent_id is None

    def test_escalated_rejects_winning_agent(self) -> None:
        with pytest.raises(ValidationError, match="winning_agent_id must be None"):
            make_resolution(
                outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
                winning_agent_id="agent-a",
            )

    def test_escalated_rejects_winning_position(self) -> None:
        with pytest.raises(ValidationError, match="winning_position must be None"):
            make_resolution(
                outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
                winning_agent_id=None,
                winning_position="Some position",
            )

    def test_resolved_requires_winner(self) -> None:
        with pytest.raises(ValidationError, match="winning_agent_id is required"):
            make_resolution(
                outcome=ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY,
                winning_agent_id=None,
            )

    def test_resolved_requires_winning_position(self) -> None:
        with pytest.raises(ValidationError, match="winning_position is required"):
            make_resolution(
                outcome=ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY,
                winning_agent_id="agent-a",
                winning_position=None,
            )


@pytest.mark.unit
class TestDissentRecord:
    def test_valid_dissent_record(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution()
        record = DissentRecord(
            id="dissent-test12345",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id="agent-b",
            dissenting_position="Use monolith",
            strategy_used=ConflictResolutionStrategy.AUTHORITY,
            timestamp=conflict.detected_at,
        )
        assert record.dissenting_agent_id == "agent-b"
        assert record.strategy_used == ConflictResolutionStrategy.AUTHORITY

    def test_metadata_tuple(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution()
        record = DissentRecord(
            id="dissent-test12345",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id="agent-b",
            dissenting_position="Use monolith",
            strategy_used=ConflictResolutionStrategy.AUTHORITY,
            timestamp=conflict.detected_at,
            metadata=(("key", "value"),),
        )
        assert record.metadata == (("key", "value"),)

    def test_frozen(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution()
        record = DissentRecord(
            id="dissent-test12345",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id="agent-b",
            dissenting_position="Use monolith",
            strategy_used=ConflictResolutionStrategy.AUTHORITY,
            timestamp=conflict.detected_at,
        )
        with pytest.raises(ValidationError):
            record.id = "changed"  # type: ignore[misc]


@pytest.mark.unit
class TestDissentRecordValidation:
    def test_dissenting_agent_not_in_positions_raises(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution()
        with pytest.raises(ValidationError, match="not found in conflict positions"):
            DissentRecord(
                id="dissent-test12345",
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id="nonexistent-agent",
                dissenting_position="Some position",
                strategy_used=ConflictResolutionStrategy.AUTHORITY,
                timestamp=conflict.detected_at,
            )

    def test_mismatched_conflict_id_raises(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution(conflict_id="conflict-different")
        with pytest.raises(ValidationError, match=r"does not match conflict\.id"):
            DissentRecord(
                id="dissent-test12345",
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id="agent-b",
                dissenting_position="Use monolith",
                strategy_used=ConflictResolutionStrategy.AUTHORITY,
                timestamp=conflict.detected_at,
            )

    def test_dissenter_equals_winner_for_non_escalated_raises(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution(winning_agent_id="agent-a")
        with pytest.raises(
            ValidationError,
            match="dissenting_agent_id must differ from winning_agent_id",
        ):
            DissentRecord(
                id="dissent-test12345",
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id="agent-a",
                dissenting_position="Use microservices",
                strategy_used=ConflictResolutionStrategy.AUTHORITY,
                timestamp=conflict.detected_at,
            )

    def test_dissenter_equals_position_ok_for_escalated(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution(
            outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by="human",
        )
        record = DissentRecord(
            id="dissent-test12345",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id="agent-a",
            dissenting_position="Use microservices",
            strategy_used=ConflictResolutionStrategy.HUMAN,
            timestamp=conflict.detected_at,
        )
        assert record.dissenting_agent_id == "agent-a"


@pytest.mark.unit
class TestConflictResolutionOutcome:
    def test_all_outcomes_exist(self) -> None:
        assert len(ConflictResolutionOutcome) == 4

    @pytest.mark.parametrize(
        "outcome",
        [
            ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY,
            ConflictResolutionOutcome.RESOLVED_BY_DEBATE,
            ConflictResolutionOutcome.RESOLVED_BY_HYBRID,
            ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        ],
    )
    def test_outcome_is_string(self, outcome: ConflictResolutionOutcome) -> None:
        assert isinstance(outcome.value, str)
