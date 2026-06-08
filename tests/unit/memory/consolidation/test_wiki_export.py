"""Tests for WikiExporter."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.config import WikiExportConfig
from synthorg.memory.consolidation.wiki_export import (
    WikiExporter,
    WikiExportResult,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend


def _make_raw_entry(entry_id: str = "det-1") -> MemoryEntry:
    content = json.dumps(
        {
            "prompt": "implement auth",
            "output": "done",
        }
    )
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content=content,
        category=MemoryCategory.EPISODIC,
        metadata=MemoryMetadata(tags=("detailed_experience",)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_compressed_entry(entry_id: str = "comp-1") -> MemoryEntry:
    content = json.dumps(
        {
            "strategic_decisions": ["Use JWT for auth"],
            "applicable_contexts": ["Web API authentication"],
        }
    )
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content=content,
        category=MemoryCategory.EPISODIC,
        metadata=MemoryMetadata(tags=("compressed_experience",)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _mock_backend(
    raw_entries: tuple[MemoryEntry, ...] = (),
    compressed_entries: tuple[MemoryEntry, ...] = (),
) -> AsyncMock:
    backend = AsyncMock()

    async def _retrieve(
        agent_id: str,
        query: object,
    ) -> tuple[MemoryEntry, ...]:
        tags = getattr(query, "tags", ())
        if "detailed_experience" in tags:
            return raw_entries
        if "compressed_experience" in tags:
            return compressed_entries
        return ()

    backend.retrieve = AsyncMock(side_effect=_retrieve)
    return backend


class TestWikiExportResult:
    """Tests for WikiExportResult model."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        r = WikiExportResult(
            raw_count=5,
            compressed_count=3,
            export_root="/data/wiki",
        )
        assert r.raw_count == 5
        assert r.compressed_count == 3


class TestWikiExporter:
    """Tests for WikiExporter."""

    @pytest.mark.unit
    async def test_export_creates_directories(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
        )
        backend = _mock_backend()
        exporter = WikiExporter(backend=backend, config=config)
        result = await exporter.export("agent-1")
        assert (tmp_path / "wiki" / "raw").is_dir()
        assert (tmp_path / "wiki" / "wiki").is_dir()
        assert (tmp_path / "wiki" / "index.md").is_file()
        assert isinstance(result, WikiExportResult)

    @pytest.mark.unit
    async def test_export_raw_entries(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
        )
        backend = _mock_backend(
            raw_entries=(_make_raw_entry("det-1"),),
        )
        exporter = WikiExporter(backend=backend, config=config)
        result = await exporter.export("agent-1")
        assert result.raw_count == 1
        raw_file = tmp_path / "wiki" / "raw" / "det-1.md"
        assert raw_file.is_file()
        content = raw_file.read_text(encoding="utf-8")
        assert "id: det-1" in content

    @pytest.mark.unit
    async def test_export_compressed_entries(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
        )
        backend = _mock_backend(
            compressed_entries=(_make_compressed_entry("comp-1"),),
        )
        exporter = WikiExporter(backend=backend, config=config)
        result = await exporter.export("agent-1")
        assert result.compressed_count == 1
        wiki_file = tmp_path / "wiki" / "wiki" / "comp-1.md"
        assert wiki_file.is_file()
        content = wiki_file.read_text(encoding="utf-8")
        assert "Strategic Decisions" in content
        assert "Use JWT for auth" in content
        assert "Applicable Contexts" in content

    @pytest.mark.unit
    async def test_export_generates_index(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
        )
        backend = _mock_backend(
            raw_entries=(_make_raw_entry(),),
            compressed_entries=(_make_compressed_entry(),),
        )
        exporter = WikiExporter(backend=backend, config=config)
        await exporter.export("agent-1")
        index = tmp_path / "wiki" / "index.md"
        content = index.read_text(encoding="utf-8")
        assert "Memory Wiki Export" in content
        assert "Raw artifacts: 1" in content
        assert "Compressed experiences: 1" in content

    @pytest.mark.unit
    async def test_export_respects_include_flags(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
            include_raw_tier=False,
            include_compressed_tier=False,
        )
        backend = _mock_backend(
            raw_entries=(_make_raw_entry(),),
            compressed_entries=(_make_compressed_entry(),),
        )
        exporter = WikiExporter(backend=backend, config=config)
        result = await exporter.export("agent-1")
        assert result.raw_count == 0
        assert result.compressed_count == 0

    @pytest.mark.unit
    async def test_export_on_backend_error_returns_zero(
        self,
        tmp_path: Path,
    ) -> None:
        config = WikiExportConfig(
            enabled=True,
            export_root=str(tmp_path / "wiki"),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("backend down"),
        )
        exporter = WikiExporter(backend=backend, config=config)
        result = await exporter.export("agent-1")
        assert result.raw_count == 0
        assert result.compressed_count == 0
