"""Unit tests for LLM-curated curation strategy."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.training.curation.llm_curated import LLMCurated
from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.providers.errors import ProviderError
from synthorg.providers.protocol import CompletionProvider


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(
    *,
    content: str = "Knowledge item",
    source_agent_id: str = "senior-1",
) -> TrainingItem:
    return TrainingItem(
        source_agent_id=source_agent_id,
        content_type=ContentType.PROCEDURAL,
        content=content,
        created_at=_now(),
    )


@pytest.mark.unit
class TestLLMCurated:
    """LLMCurated strategy tests."""

    def test_name(self) -> None:
        curation = LLMCurated()
        assert curation.name == "llm_curated"

    async def test_falls_back_when_no_provider(self) -> None:
        curation = LLMCurated(provider=None)
        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        # Should use fallback (RelevanceScoreCuration)
        assert len(result) == 5
        assert all(item.relevance_score >= 0.0 for item in result)

    async def test_empty_input(self) -> None:
        curation = LLMCurated()
        result = await curation.curate(
            (),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert result == ()

    async def test_provider_success(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        response = MagicMock()
        response.content = "0, 2"
        provider.complete.return_value = response

        items = tuple(_make_item(content=f"Item {i}") for i in range(4))
        curation = LLMCurated(provider=provider, top_k=10)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        # Should select indices 0 and 2
        assert len(result) == 2
        assert result[0].content == "Item 0"
        assert result[1].content == "Item 2"
        provider.complete.assert_awaited_once()

    async def test_provider_error_falls_back(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.side_effect = ProviderError("provider unavailable")

        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        curation = LLMCurated(provider=provider, top_k=3)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        # Should fall back to RelevanceScoreCuration
        assert len(result) == 3

    async def test_empty_llm_response_falls_back(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        response = MagicMock()
        response.content = "no valid indices here"
        provider.complete.return_value = response

        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        curation = LLMCurated(provider=provider, top_k=3)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        # No valid indices parsed, should fall back
        assert len(result) == 3

    def test_parse_indices_valid(self) -> None:
        result = LLMCurated._parse_indices("0, 2, 4", max_index=5)
        assert result == [0, 2, 4]

    def test_parse_indices_deduplicates(self) -> None:
        result = LLMCurated._parse_indices("1, 1, 3", max_index=5)
        assert result == [1, 3]

    def test_parse_indices_filters_out_of_range(self) -> None:
        result = LLMCurated._parse_indices("0, 10, 2", max_index=5)
        assert result == [0, 2]

    def test_parse_indices_empty_text(self) -> None:
        result = LLMCurated._parse_indices("", max_index=5)
        assert result == []
