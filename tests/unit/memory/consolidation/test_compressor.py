"""Tests for ExperienceCompressor protocol and GEMS models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.memory.consolidation.compressor import ExperienceCompressor
from synthorg.memory.consolidation.models import (
    CompressedExperience,
    DetailedExperience,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata


def _make_compressed(
    *,
    decisions: tuple[str, ...] = ("Used caching for repeated lookups",),
    ratio: float = 0.3,
    source_artifact_ids: tuple[str, ...] = ("det-1",),
) -> CompressedExperience:
    return CompressedExperience(
        id="comp-1",
        agent_id="agent-1",
        strategic_decisions=decisions,
        applicable_contexts=("When dealing with database queries",),
        source_artifact_ids=source_artifact_ids,
        compression_ratio=ratio,
        compressor_version="llm-v1",
        metadata=MemoryMetadata(tags=("compressed_experience",)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestDetailedExperience:
    """Tests for DetailedExperience model."""

    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        d = DetailedExperience(
            id="det-1",
            agent_id="agent-1",
            prompt="implement auth",
            output="done",
            metadata=MemoryMetadata(tags=("detailed_experience",)),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert d.prompt == "implement auth"
        assert d.verification_feedback is None
        assert d.reasoning_trace == ()
        assert d.source_task_id is None

    @pytest.mark.unit
    def test_full_construction(self) -> None:
        d = DetailedExperience(
            id="det-1",
            agent_id="agent-1",
            prompt="implement auth",
            output="implemented JWT",
            verification_feedback="all tests pass",
            reasoning_trace=("step 1", "step 2"),
            metadata=MemoryMetadata(tags=("detailed_experience",)),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            source_task_id="task-42",
        )
        assert d.verification_feedback == "all tests pass"
        assert len(d.reasoning_trace) == 2
        assert d.source_task_id == "task-42"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        d = DetailedExperience(
            id="det-1",
            agent_id="agent-1",
            prompt="p",
            output="o",
            metadata=MemoryMetadata(tags=("detailed_experience",)),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            d.prompt = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DetailedExperience(
                id="det-1",
                agent_id="agent-1",
                prompt="",
                output="o",
                metadata=MemoryMetadata(tags=("detailed_experience",)),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )


class TestCompressedExperience:
    """Tests for CompressedExperience model."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        c = _make_compressed()
        assert c.strategic_decisions == ("Used caching for repeated lookups",)
        assert c.compression_ratio == 0.3
        assert c.compressor_version == "llm-v1"

    @pytest.mark.unit
    def test_empty_strategic_decisions_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="strategic_decisions must contain at least one entry",
        ):
            _make_compressed(decisions=())

    @pytest.mark.unit
    def test_empty_source_artifact_ids_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="source_artifact_ids must contain at least one entry",
        ):
            _make_compressed(source_artifact_ids=())

    @pytest.mark.unit
    def test_compression_ratio_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _make_compressed(ratio=0.0)
        with pytest.raises(ValidationError):
            _make_compressed(ratio=1.1)
        # Valid edge: exactly 1.0
        c = _make_compressed(ratio=1.0)
        assert c.compression_ratio == 1.0

    @pytest.mark.unit
    def test_frozen(self) -> None:
        c = _make_compressed()
        with pytest.raises(ValidationError):
            c.compression_ratio = 0.5  # type: ignore[misc]


class TestExperienceCompressorProtocol:
    """Tests for ExperienceCompressor protocol compliance."""

    @pytest.mark.unit
    def test_protocol_is_runtime_checkable(self) -> None:
        class _StubCompressor:
            async def compress(
                self,
                prompt: str,
                output: str,
                verification_feedback: str | None,
                reasoning_trace: tuple[str, ...],
                memory_context: tuple[MemoryEntry, ...],
                *,
                agent_id: str = "unknown",
                source_artifact_ids: tuple[str, ...],
            ) -> CompressedExperience:
                return _make_compressed()

        stub = _StubCompressor()
        assert isinstance(stub, ExperienceCompressor)
