"""Unit tests for training mode curation strategies."""

from datetime import UTC, datetime

import pytest

from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.curation.relevance import (
    RelevanceScoreCuration,
)
from synthorg.hr.training.models import ContentType, TrainingItem


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(
    *,
    content: str = "Knowledge item",
    relevance_score: float = 0.0,
    source_agent_id: str = "senior-1",
) -> TrainingItem:
    return TrainingItem(
        source_agent_id=source_agent_id,
        content_type=ContentType.PROCEDURAL,
        content=content,
        relevance_score=relevance_score,
        created_at=_now(),
    )


@pytest.mark.unit
class TestRelevanceScoreCuration:
    """RelevanceScoreCuration strategy tests."""

    def test_name(self) -> None:
        curation = RelevanceScoreCuration()
        assert curation.name == "relevance"

    async def test_scores_and_ranks_items(self) -> None:
        items = tuple(_make_item(content=f"Item {i}") for i in range(10))
        curation = RelevanceScoreCuration(top_k=5)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert len(result) == 5
        # Items should have relevance scores assigned
        assert all(item.relevance_score >= 0.0 for item in result)
        # Should be sorted descending
        scores = [item.relevance_score for item in result]
        assert scores == sorted(scores, reverse=True)

    async def test_returns_all_when_fewer_than_top_k(self) -> None:
        items = (_make_item(content="Only one"),)
        curation = RelevanceScoreCuration(top_k=50)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert len(result) == 1

    async def test_empty_input(self) -> None:
        curation = RelevanceScoreCuration()
        result = await curation.curate(
            (),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert result == ()

    async def test_default_top_k_is_fifty(self) -> None:
        items = tuple(_make_item(content=f"Item {i}") for i in range(60))
        curation = RelevanceScoreCuration()
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert len(result) == 50
