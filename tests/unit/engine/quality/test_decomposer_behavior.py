"""Behavioral tests for criteria decomposer implementations."""

import pytest

from synthorg.core.task import AcceptanceCriterion
from synthorg.engine.quality.decomposers.identity import (
    IdentityCriteriaDecomposer,
)
from synthorg.engine.quality.decomposers.llm import LLMCriteriaDecomposer


@pytest.mark.unit
class TestIdentityDecomposerBehavior:
    async def test_produces_one_probe_per_criterion(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        criteria = (
            AcceptanceCriterion(description="Feature A"),
            AcceptanceCriterion(description="Feature B"),
        )
        result = await decomposer.decompose(
            criteria, task_id="task-1", agent_id="agent-1"
        )
        assert len(result) == 2

    async def test_probe_ids_contain_task_id(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        criteria = (AcceptanceCriterion(description="Done"),)
        result = await decomposer.decompose(
            criteria, task_id="task-123", agent_id="agent-1"
        )
        assert result[0].id == "task-123-probe-0"

    async def test_probe_text_contains_criterion(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        criteria = (AcceptanceCriterion(description="Button visible"),)
        result = await decomposer.decompose(criteria, task_id="t1", agent_id="a1")
        assert "Button visible" in result[0].probe_text

    async def test_source_criterion_matches(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        criteria = (AcceptanceCriterion(description="Feature complete"),)
        result = await decomposer.decompose(criteria, task_id="t1", agent_id="a1")
        assert result[0].source_criterion == "Feature complete"

    async def test_empty_criteria_produces_empty_probes(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        result = await decomposer.decompose((), task_id="t1", agent_id="a1")
        assert result == ()

    async def test_multiple_criteria_sequential_ids(self) -> None:
        decomposer = IdentityCriteriaDecomposer()
        criteria = (
            AcceptanceCriterion(description="A"),
            AcceptanceCriterion(description="B"),
            AcceptanceCriterion(description="C"),
        )
        result = await decomposer.decompose(criteria, task_id="t1", agent_id="a1")
        assert [p.id for p in result] == [
            "t1-probe-0",
            "t1-probe-1",
            "t1-probe-2",
        ]


@pytest.mark.unit
class TestLLMDecomposerBehavior:
    async def test_name_property(self) -> None:
        from tests.unit.providers.conftest import FakeProvider

        decomposer = LLMCriteriaDecomposer(
            provider=FakeProvider(),
            model_id="test-capable-001",
        )
        assert decomposer.name == "llm"
