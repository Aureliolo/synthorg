"""Tests for abstractive summarizer."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider

_NOW = datetime.now(UTC)


def _make_provider(content: str = "Summary text.") -> AsyncMock:
    """Create a mock CompletionProvider that returns given content."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content=content,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
            model="test-small-001",
        ),
    )
    return provider


def _make_entry(entry_id: str, content: str = "Some content") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="test-agent",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=_NOW,
    )


@pytest.mark.unit
class TestAbstractiveSummarizer:
    """AbstractiveSummarizer LLM-based summarization."""

    async def test_successful_summarization(self) -> None:
        provider = _make_provider("A concise summary.")
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        result = await summarizer.summarize("Long text about meetings and plans.")
        assert result == "A concise summary."
        provider.complete.assert_called_once()

    async def test_uses_system_and_user_messages(self) -> None:
        provider = _make_provider()
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        await summarizer.summarize("Test content")
        call_args = provider.complete.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert messages[0].role == MessageRole.SYSTEM
        assert messages[1].role == MessageRole.USER
        assert "Test content" in messages[1].content

    async def test_uses_configured_model(self) -> None:
        provider = _make_provider()
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-medium-001",
        )
        await summarizer.summarize("Content")
        call_args = provider.complete.call_args
        assert call_args[0][1] == "test-medium-001"

    async def test_fallback_on_provider_error(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        result = await summarizer.summarize("A" * 300)
        # Should fall back to truncation
        assert len(result) <= 203  # 200 + "..."
        assert result.endswith("...")

    async def test_fallback_on_empty_response(self) -> None:
        provider = _make_provider(content="")
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        result = await summarizer.summarize("Original content")
        # Empty response triggers truncation fallback; content is
        # shorter than _TRUNCATE_LENGTH so returned verbatim.
        assert result == "Original content"

    async def test_blank_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            AbstractiveSummarizer(provider=_make_provider(), model="")
        with pytest.raises(ValueError, match="non-blank"):
            AbstractiveSummarizer(provider=_make_provider(), model="   ")

    async def test_memory_error_propagates(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=MemoryError("out of memory"))
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        with pytest.raises(MemoryError):
            await summarizer.summarize("Some content")

    async def test_recursion_error_propagates(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RecursionError("max depth"))
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        with pytest.raises(RecursionError):
            await summarizer.summarize("Some content")


@pytest.mark.unit
class TestAbstractiveSummarizerBatch:
    """AbstractiveSummarizer.summarize_batch behaviour."""

    async def test_batch_returns_id_summary_pairs(self) -> None:
        provider = _make_provider("Batch summary.")
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        entries = (
            _make_entry("m1", "First memory"),
            _make_entry("m2", "Second memory"),
        )
        result = await summarizer.summarize_batch(entries)
        assert len(result) == 2
        assert result[0] == ("m1", "Batch summary.")
        assert result[1] == ("m2", "Batch summary.")

    async def test_batch_continues_on_individual_failure(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            side_effect=[
                CompletionResponse(
                    content="Good summary",
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(
                        input_tokens=10,
                        output_tokens=5,
                        cost=0.001,
                    ),
                    model="test-small-001",
                ),
                RuntimeError("LLM error"),
            ],
        )
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        entries = (
            _make_entry("m1", "First"),
            _make_entry("m2", "Second"),
        )
        result = await summarizer.summarize_batch(entries)
        assert len(result) == 2
        assert result[0][1] == "Good summary"
        # Second entry falls back to truncation; "Second" is shorter
        # than _TRUNCATE_LENGTH so returned verbatim.
        assert result[1][1] == "Second"

    async def test_empty_batch(self) -> None:
        provider = _make_provider()
        summarizer = AbstractiveSummarizer(
            provider=provider,
            model="test-small-001",
        )
        result = await summarizer.summarize_batch(())
        assert result == ()
