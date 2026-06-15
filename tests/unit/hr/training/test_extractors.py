"""Unit tests for training mode content extractors."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.extractors.memory_backed import (
    MemoryBackedExtractor,
)
from synthorg.hr.training.extractors.tool_patterns import (
    ToolPatternExtractor,
)
from synthorg.hr.training.models import ContentType
from synthorg.memory.protocol import MemoryBackend
from synthorg.tools.invocation_tracker import ToolInvocationTracker


def _now() -> datetime:
    return datetime.now(UTC)


def _make_memory_entry(
    *,
    memory_id: str = "mem-1",
    agent_id: str = "senior-1",
    category: MemoryCategory = MemoryCategory.PROCEDURAL,
    content: str = "Always validate inputs before processing",
    tags: tuple[str, ...] = (),
) -> MagicMock:
    """Create a mock MemoryEntry."""
    entry = MagicMock()
    entry.id = memory_id
    entry.agent_id = agent_id
    entry.category = category
    entry.content = content
    entry.metadata = MagicMock()
    entry.metadata.tags = tags
    entry.created_at = _now()
    return entry


def _make_tool_record(
    *,
    agent_id: str = "senior-1",
    tool_name: str = "api_tool",
    is_success: bool = True,
) -> MagicMock:
    """Create a mock ToolInvocationRecord."""
    record = MagicMock()
    record.agent_id = agent_id
    record.tool_name = tool_name
    record.is_success = is_success
    record.timestamp = _now()
    return record


# -- ProceduralMemoryExtractor ----------------------------------------


@pytest.mark.unit
class TestProceduralMemoryExtractor:
    """ProceduralMemoryExtractor tests."""

    def test_content_type(self) -> None:
        extractor = MemoryBackedExtractor(
            backend=AsyncMock(spec=MemoryBackend),
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        )
        assert extractor.content_type == ContentType.PROCEDURAL

    async def test_extracts_procedural_memories(self) -> None:
        entries = (
            _make_memory_entry(memory_id="m1", content="Lesson 1"),
            _make_memory_entry(memory_id="m2", content="Lesson 2"),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = entries

        extractor = MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        )
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(items) == 2
        assert items[0].content_type == ContentType.PROCEDURAL
        assert items[0].source_agent_id == "senior-1"
        assert items[0].source_memory_id == "m1"

    async def test_extracts_from_multiple_agents(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.side_effect = [
            (_make_memory_entry(agent_id="s1", content="A"),),
            (_make_memory_entry(agent_id="s2", content="B"),),
        ]

        extractor = MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        )
        items = await extractor.extract(
            source_agent_ids=("s1", "s2"),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(items) == 2

    async def test_returns_empty_for_no_agents(self) -> None:
        extractor = MemoryBackedExtractor(
            backend=AsyncMock(spec=MemoryBackend),
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        )
        items = await extractor.extract(
            source_agent_ids=(),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert items == ()

    async def test_returns_empty_when_no_memories(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = ()

        extractor = MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        )
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert items == ()


# -- SemanticMemoryExtractor ------------------------------------------


@pytest.mark.unit
class TestSemanticMemoryExtractor:
    """SemanticMemoryExtractor tests."""

    def test_content_type(self) -> None:
        extractor = MemoryBackedExtractor(
            backend=AsyncMock(spec=MemoryBackend),
            memory_category=MemoryCategory.SEMANTIC,
            content_type=ContentType.SEMANTIC,
        )
        assert extractor.content_type == ContentType.SEMANTIC

    async def test_extracts_semantic_memories(self) -> None:
        entries = (
            _make_memory_entry(
                memory_id="s1",
                content="Domain knowledge",
                category=MemoryCategory.SEMANTIC,
            ),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = entries

        extractor = MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.SEMANTIC,
            content_type=ContentType.SEMANTIC,
        )
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(items) == 1
        assert items[0].content_type == ContentType.SEMANTIC
        assert items[0].content == "Domain knowledge"


# -- ToolPatternExtractor ---------------------------------------------


@pytest.mark.unit
class TestToolPatternExtractor:
    """ToolPatternExtractor tests."""

    def test_content_type(self) -> None:
        extractor = ToolPatternExtractor(tracker=AsyncMock(spec=ToolInvocationTracker))
        assert extractor.content_type == ContentType.TOOL_PATTERNS

    async def test_extracts_tool_patterns(self) -> None:
        records = (
            _make_tool_record(tool_name="api_tool", is_success=True),
            _make_tool_record(tool_name="api_tool", is_success=True),
            _make_tool_record(tool_name="api_tool", is_success=False),
            _make_tool_record(tool_name="db_tool", is_success=True),
        )
        tracker = AsyncMock(spec=ToolInvocationTracker)
        tracker.get_records.return_value = records

        extractor = ToolPatternExtractor(tracker=tracker)
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        # Should produce 2 patterns: api_tool and db_tool
        assert len(items) == 2
        assert all(i.content_type == ContentType.TOOL_PATTERNS for i in items)

    async def test_computes_success_rate(self) -> None:
        records = (
            _make_tool_record(tool_name="api", is_success=True),
            _make_tool_record(tool_name="api", is_success=True),
            _make_tool_record(tool_name="api", is_success=False),
        )
        tracker = AsyncMock(spec=ToolInvocationTracker)
        tracker.get_records.return_value = records

        extractor = ToolPatternExtractor(tracker=tracker)
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(items) == 1
        # Content should mention 66% or 67% success rate
        assert "api" in items[0].content.lower()

    async def test_returns_empty_for_no_records(self) -> None:
        tracker = AsyncMock(spec=ToolInvocationTracker)
        tracker.get_records.return_value = ()

        extractor = ToolPatternExtractor(tracker=tracker)
        items = await extractor.extract(
            source_agent_ids=("senior-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert items == ()
