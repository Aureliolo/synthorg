"""Tests for call classification service."""

import pytest
from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.call_classifier import (
    ClassificationContext,
    RulesBasedClassifier,
    classify_call,
)


def _ctx(  # noqa: PLR0913
    *,
    turn_number: int = 1,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    is_delegation: bool = False,
    is_review: bool = False,
    is_group_discussion: bool = False,
    is_planning_phase: bool = False,
    is_system_prompt: bool = False,
    is_embedding_operation: bool = False,
    is_quality_judge: bool = False,
    tool_calls_made: tuple[str, ...] = (),
    agent_role: str | None = None,
) -> ClassificationContext:
    return ClassificationContext(
        turn_number=turn_number,
        agent_id=agent_id,
        task_id=task_id,
        is_delegation=is_delegation,
        is_review=is_review,
        is_group_discussion=is_group_discussion,
        is_planning_phase=is_planning_phase,
        is_system_prompt=is_system_prompt,
        is_embedding_operation=is_embedding_operation,
        is_quality_judge=is_quality_judge,
        tool_calls_made=tool_calls_made,
        agent_role=agent_role,
    )


@pytest.mark.unit
class TestClassificationContext:
    """ClassificationContext model validation."""

    def test_basic_construction(self) -> None:
        ctx = _ctx()
        assert ctx.agent_id == "agent-1"
        assert ctx.task_id == "task-1"
        assert ctx.turn_number == 1
        assert ctx.is_delegation is False

    def test_frozen(self) -> None:
        ctx = _ctx()
        with pytest.raises(ValidationError):
            ctx.is_delegation = True  # type: ignore[misc]

    def test_agent_role_optional(self) -> None:
        ctx = _ctx(agent_role=None)
        assert ctx.agent_role is None
        ctx2 = _ctx(agent_role="orchestrator")
        assert ctx2.agent_role == "orchestrator"

    def test_turn_number_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _ctx(turn_number=0)

    def test_agent_id_must_not_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            _ctx(agent_id="   ")

    def test_task_id_must_not_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            _ctx(task_id="")


@pytest.mark.unit
class TestRulesBasedClassifier:
    """RulesBasedClassifier priority ordering and category logic."""

    def setup_method(self) -> None:
        self.classifier = RulesBasedClassifier()

    def test_all_false_returns_productive(self) -> None:
        ctx = _ctx()
        assert self.classifier.classify(ctx) == LLMCallCategory.PRODUCTIVE

    def test_embedding_wins_over_everything(self) -> None:
        ctx = _ctx(
            is_embedding_operation=True,
            is_delegation=True,
            is_planning_phase=True,
        )
        assert self.classifier.classify(ctx) == LLMCallCategory.EMBEDDING

    def test_coordination_wins_over_system_and_productive(self) -> None:
        ctx = _ctx(is_delegation=True, is_planning_phase=True)
        assert self.classifier.classify(ctx) == LLMCallCategory.COORDINATION

    def test_system_wins_over_productive(self) -> None:
        ctx = _ctx(is_planning_phase=True)
        assert self.classifier.classify(ctx) == LLMCallCategory.SYSTEM

    def test_delegation_maps_to_coordination(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_delegation=True))
            == LLMCallCategory.COORDINATION
        )

    def test_review_maps_to_coordination(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_review=True))
            == LLMCallCategory.COORDINATION
        )

    def test_meeting_maps_to_coordination(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_group_discussion=True))
            == LLMCallCategory.COORDINATION
        )

    def test_planning_phase_maps_to_system(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_planning_phase=True))
            == LLMCallCategory.SYSTEM
        )

    def test_system_prompt_maps_to_system(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_system_prompt=True))
            == LLMCallCategory.SYSTEM
        )

    def test_quality_judge_maps_to_system(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_quality_judge=True))
            == LLMCallCategory.SYSTEM
        )

    def test_embedding_alone(self) -> None:
        assert (
            self.classifier.classify(_ctx(is_embedding_operation=True))
            == LLMCallCategory.EMBEDDING
        )

    def test_embedding_priority_over_coordination(self) -> None:
        ctx = _ctx(is_embedding_operation=True, is_review=True)
        assert self.classifier.classify(ctx) == LLMCallCategory.EMBEDDING

    def test_embedding_priority_over_system(self) -> None:
        ctx = _ctx(is_embedding_operation=True, is_quality_judge=True)
        assert self.classifier.classify(ctx) == LLMCallCategory.EMBEDDING

    def test_coordination_priority_ordering_with_all_true(self) -> None:
        """When all coordination flags set, COORDINATION wins over SYSTEM."""
        ctx = _ctx(is_delegation=True, is_review=True, is_quality_judge=True)
        assert self.classifier.classify(ctx) == LLMCallCategory.COORDINATION

    def test_tool_calls_and_agent_role_do_not_affect_classification(self) -> None:
        """Metadata fields that carry context but don't change priority."""
        ctx = _ctx(
            tool_calls_made=("read_file", "write_file"),
            agent_role="orchestrator",
        )
        assert self.classifier.classify(ctx) == LLMCallCategory.PRODUCTIVE


@pytest.mark.unit
class TestClassifyCallConvenienceFunction:
    """classify_call() uses the default RulesBasedClassifier."""

    def test_productive_default(self) -> None:
        assert classify_call(_ctx()) == LLMCallCategory.PRODUCTIVE

    def test_embedding_via_convenience(self) -> None:
        assert (
            classify_call(_ctx(is_embedding_operation=True))
            == LLMCallCategory.EMBEDDING
        )

    def test_coordination_via_convenience(self) -> None:
        assert classify_call(_ctx(is_delegation=True)) == LLMCallCategory.COORDINATION

    def test_system_via_convenience(self) -> None:
        assert classify_call(_ctx(is_planning_phase=True)) == LLMCallCategory.SYSTEM

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ({"is_embedding_operation": True}, LLMCallCategory.EMBEDDING),
            ({"is_delegation": True}, LLMCallCategory.COORDINATION),
            ({"is_review": True}, LLMCallCategory.COORDINATION),
            ({"is_group_discussion": True}, LLMCallCategory.COORDINATION),
            ({"is_planning_phase": True}, LLMCallCategory.SYSTEM),
            ({"is_system_prompt": True}, LLMCallCategory.SYSTEM),
            ({"is_quality_judge": True}, LLMCallCategory.SYSTEM),
            ({}, LLMCallCategory.PRODUCTIVE),
        ],
    )
    def test_parametrized_priority_table(
        self,
        flags: dict[str, bool],
        expected: LLMCallCategory,
    ) -> None:
        assert classify_call(_ctx(**flags)) == expected  # type: ignore[arg-type]
