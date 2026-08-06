"""Unit tests for LLM-curated curation strategy."""

from datetime import UTC, datetime
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.hr.training.curation.llm_curated import LLMCurated
from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.providers.errors import DriverNotRegisteredError, ProviderError
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared.mock_of import mock_of
from tests._shared.model_binding import (
    bound_ref,
    connections,
    model_ref_resolver,
    one_connection,
)

_CURATION_KEY: Final[tuple[str, str]] = ("hr", "training_curation_model")


def _now() -> datetime:
    return datetime.now(UTC)


def _bound(provider: AsyncMock, **kwargs: object) -> LLMCurated:
    """Build a strategy whose live pair resolves to *provider*.

    Returns:
        The strategy under test, bound to the operator-chosen pair.
    """
    return LLMCurated(
        connections=one_connection(provider),
        config_resolver=model_ref_resolver(),
        **kwargs,  # type: ignore[arg-type]
    )


def _unbound(provider: AsyncMock) -> LLMCurated:
    """Build a strategy whose curation pair is unassigned.

    Returns:
        The strategy under test, with nothing to dispatch on.
    """
    return LLMCurated(
        connections=one_connection(provider),
        config_resolver=model_ref_resolver(default=""),
    )


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

    async def test_falls_back_when_pair_unassigned(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        curation = _unbound(provider)
        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            content_type=ContentType.PROCEDURAL,
        )
        # Should use fallback (RelevanceScoreCuration)
        assert len(result) == 5
        assert all(item.relevance_score >= 0.0 for item in result)
        provider.complete.assert_not_awaited()

    async def test_falls_back_when_no_connection_selector(self) -> None:
        curation = LLMCurated(config_resolver=model_ref_resolver())
        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            content_type=ContentType.PROCEDURAL,
        )
        assert len(result) == 5

    async def test_empty_input(self) -> None:
        curation = LLMCurated()
        result = await curation.curate(
            (),
            new_agent_role="engineer",
            content_type=ContentType.PROCEDURAL,
        )
        assert result == ()

    async def test_provider_success(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        response = MagicMock()
        response.content = "0, 2"
        provider.complete.return_value = response

        items = tuple(_make_item(content=f"Item {i}") for i in range(4))
        curation = _bound(provider, top_k=10)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            content_type=ContentType.PROCEDURAL,
        )
        # Should select indices 0 and 2
        assert len(result) == 2
        assert result[0].content == "Item 0"
        assert result[1].content == "Item 2"
        provider.complete.assert_awaited_once()

    async def test_reassignment_takes_effect_on_the_next_curation(self) -> None:
        # The pair is read per curation call, so an operator reassigning it
        # arms the next curation rather than the next boot.
        first = AsyncMock(spec=CompletionProvider)
        first.complete.return_value = MagicMock(content="0")
        second = AsyncMock(spec=CompletionProvider)
        second.complete.return_value = MagicMock(content="1")
        assigned = bound_ref(provider="first")
        stored: dict[tuple[str, str], str] = {_CURATION_KEY: assigned}

        async def _get_str(namespace: str, key: str) -> str:
            return stored[(namespace, key)]

        resolver: ConfigResolverProtocol = mock_of[ConfigResolverProtocol](
            get_str=AsyncMock(side_effect=_get_str),
        )
        curation = LLMCurated(
            connections=connections({"first": first, "second": second}),
            config_resolver=resolver,
        )
        items = tuple(_make_item(content=f"Item {i}") for i in range(2))

        await curation.curate(
            items, new_agent_role="engineer", content_type=ContentType.PROCEDURAL
        )
        first.complete.assert_awaited_once()

        stored[_CURATION_KEY] = bound_ref(provider="second")
        await curation.curate(
            items, new_agent_role="engineer", content_type=ContentType.PROCEDURAL
        )
        second.complete.assert_awaited_once()
        first.complete.assert_awaited_once()

    async def test_unregistered_connection_falls_back(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)

        def _select(name: str) -> CompletionProvider:
            # What the real registry raises for a name it does not hold.
            raise DriverNotRegisteredError(name)

        curation = LLMCurated(
            connections=_select,
            config_resolver=model_ref_resolver(default=bound_ref(provider="gone")),
            top_k=3,
        )
        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        result = await curation.curate(
            items,
            new_agent_role="engineer",
            content_type=ContentType.PROCEDURAL,
        )
        assert len(result) == 3
        provider.complete.assert_not_awaited()

    async def test_provider_error_falls_back(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.side_effect = ProviderError("provider unavailable")

        items = tuple(_make_item(content=f"Item {i}") for i in range(5))
        curation = _bound(provider, top_k=3)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
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
        curation = _bound(provider, top_k=3)
        result = await curation.curate(
            items,
            new_agent_role="engineer",
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
