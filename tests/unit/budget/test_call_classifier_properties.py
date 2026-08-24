"""Property-based tests for call classification."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.call_classifier import (
    ClassificationContext,
    classify_call,
)

_CATEGORY_VALUES = set(LLMCallCategory)


def _ctx_strategy() -> st.SearchStrategy[ClassificationContext]:
    return st.builds(
        ClassificationContext,
        turn_number=st.integers(min_value=1, max_value=1000),
        agent_id=st.from_regex(r"agent-[a-z0-9]{1,8}", fullmatch=True),
        task_id=st.from_regex(r"task-[a-z0-9]{1,8}", fullmatch=True),
        is_delegation=st.booleans(),
        is_review=st.booleans(),
        is_group_discussion=st.booleans(),
        is_planning_phase=st.booleans(),
        is_system_prompt=st.booleans(),
        is_embedding_operation=st.booleans(),
        is_quality_judge=st.booleans(),
        tool_calls_made=st.just(()),
        agent_role=st.none(),
    )


@pytest.mark.unit
class TestCallClassifierProperties:
    """Invariants for the call classification service."""

    @given(_ctx_strategy())
    def test_classify_always_returns_valid_category(
        self, ctx: ClassificationContext
    ) -> None:
        """classify_call always returns one of the 4 LLMCallCategory values."""
        result = classify_call(ctx)
        assert result in _CATEGORY_VALUES

    @given(
        st.builds(
            ClassificationContext,
            turn_number=st.integers(min_value=1, max_value=1000),
            agent_id=st.from_regex(r"agent-[a-z0-9]{1,8}", fullmatch=True),
            task_id=st.from_regex(r"task-[a-z0-9]{1,8}", fullmatch=True),
            is_embedding_operation=st.just(True),
            is_delegation=st.booleans(),
            is_review=st.booleans(),
            is_group_discussion=st.booleans(),
            is_planning_phase=st.booleans(),
            is_system_prompt=st.booleans(),
            is_quality_judge=st.booleans(),
            tool_calls_made=st.just(()),
            agent_role=st.none(),
        )
    )
    def test_embedding_always_wins_when_flag_set(
        self, ctx: ClassificationContext
    ) -> None:
        """EMBEDDING wins regardless of other flags when is_embedding_operation=True."""
        assert classify_call(ctx) == LLMCallCategory.EMBEDDING

    @given(
        st.builds(
            ClassificationContext,
            turn_number=st.integers(min_value=1, max_value=1000),
            agent_id=st.from_regex(r"agent-[a-z0-9]{1,8}", fullmatch=True),
            task_id=st.from_regex(r"task-[a-z0-9]{1,8}", fullmatch=True),
            is_embedding_operation=st.just(False),
            is_delegation=st.just(False),
            is_review=st.just(False),
            is_group_discussion=st.just(False),
            is_planning_phase=st.just(False),
            is_system_prompt=st.just(False),
            is_quality_judge=st.just(False),
            tool_calls_made=st.just(()),
            agent_role=st.none(),
        )
    )
    def test_productive_when_no_flags_set(self, ctx: ClassificationContext) -> None:
        """PRODUCTIVE when all boolean flags are False."""
        assert classify_call(ctx) == LLMCallCategory.PRODUCTIVE

    @given(
        st.builds(
            ClassificationContext,
            turn_number=st.integers(min_value=1, max_value=1000),
            agent_id=st.from_regex(r"agent-[a-z0-9]{1,8}", fullmatch=True),
            task_id=st.from_regex(r"task-[a-z0-9]{1,8}", fullmatch=True),
            is_embedding_operation=st.just(False),
            is_delegation=st.booleans(),
            is_review=st.booleans(),
            is_group_discussion=st.booleans(),
            is_planning_phase=st.booleans(),
            is_system_prompt=st.booleans(),
            is_quality_judge=st.booleans(),
            tool_calls_made=st.just(()),
            agent_role=st.none(),
        ).filter(lambda c: c.is_delegation or c.is_review or c.is_group_discussion)
    )
    def test_coordination_wins_over_system(self, ctx: ClassificationContext) -> None:
        """COORDINATION wins over SYSTEM when any coordination flag is True."""
        assert classify_call(ctx) == LLMCallCategory.COORDINATION
