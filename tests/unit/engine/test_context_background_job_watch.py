"""Tests for the background-job stall-nudge watch channel on AgentContext."""

from datetime import UTC, datetime

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.background_job_watch_channel import (
    BackgroundJobWatchChannel,
    WatchedJobRecord,
)
from synthorg.engine.context import AgentContext

_SINCE = datetime(2026, 4, 14, tzinfo=UTC)


@pytest.mark.unit
class TestAgentContextBackgroundJobWatch:
    """BackgroundJobWatchChannel field on AgentContext."""

    def test_default_is_empty_channel(self, sample_agent: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        assert isinstance(ctx.background_job_watch, BackgroundJobWatchChannel)
        assert ctx.background_job_watch.records == ()

    def test_with_background_job_watched_adds_a_record(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        updated = ctx.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_SINCE
        )
        record = updated.background_job_watch.get("job-1")
        assert record is not None
        assert record.started_watching_at == _SINCE
        assert record.last_nudged_at is None
        # Original unchanged
        assert ctx.background_job_watch.records == ()

    def test_with_background_job_watched_is_idempotent(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        once = ctx.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_SINCE
        )
        later = datetime(2026, 4, 14, 1, 0, tzinfo=UTC)
        twice = once.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=later
        )
        assert twice is once
        record = twice.background_job_watch.get("job-1")
        assert record is not None
        assert record.started_watching_at == _SINCE

    def test_with_background_job_watch_replaces_the_channel(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        record = WatchedJobRecord(
            job_id=NotBlankStr("job-1"), started_watching_at=_SINCE
        )
        channel = ctx.background_job_watch.with_record(record)
        ctx2 = ctx.with_background_job_watch(channel)
        assert len(ctx2.background_job_watch.records) == 1
        assert len(ctx.background_job_watch.records) == 0

    def test_survives_model_copy(self, sample_agent: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        ctx2 = ctx.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_SINCE
        )
        ctx3 = ctx2.model_copy(update={"turn_count": ctx2.turn_count})
        assert len(ctx3.background_job_watch.records) == 1

    def test_survives_with_compression(self, sample_agent: AgentIdentity) -> None:
        """State channel must not be touched by compaction."""
        from synthorg.engine.compaction.models import CompressionMetadata

        ctx = AgentContext.from_identity(sample_agent)
        ctx = ctx.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_SINCE
        )
        metadata = CompressionMetadata(
            compression_point=5,
            archived_turns=5,
            summary_tokens=200,
        )
        ctx_compressed = ctx.with_compression(
            metadata=metadata,
            compressed_conversation=(),
            fill_tokens=100,
        )
        assert len(ctx_compressed.background_job_watch.records) == 1
        assert ctx_compressed.background_job_watch.records[0].job_id == "job-1"

    def test_roundtrip_through_json(self, sample_agent: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        ctx = ctx.with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_SINCE
        )
        restored = AgentContext.model_validate_json(ctx.model_dump_json())
        assert restored.background_job_watch.get("job-1") is not None

    def test_old_checkpoint_without_field_deserialises(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        data = ctx.model_dump(mode="json")
        data.pop("background_job_watch", None)
        restored = AgentContext.model_validate(data)
        assert restored.background_job_watch.records == ()
