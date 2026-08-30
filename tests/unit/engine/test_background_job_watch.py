"""Tests for the background-job stall nudge (BackgroundJobWatcher)."""

from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.background_job_watch import (
    BackgroundJobStalenessConfig,
    BackgroundJobWatcher,
    check_background_job_watch,
    create_background_job_watcher,
)
from synthorg.engine.context import AgentContext
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.providers.enums import MessageRole
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from tests._shared.fake_background_job_repo import (
    InMemoryBackgroundJobRepository as _InMemoryBackgroundJobRepository,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_START = datetime(2026, 1, 1, tzinfo=UTC)
_CONFIG = BackgroundJobStalenessConfig(enabled=True, nudge_after_seconds=60.0)


def _job_record(
    job_id: str,
    *,
    status: BackgroundJobStatus = BackgroundJobStatus.RUNNING,
    command_repr: str = "sleep 300",
) -> BackgroundJobRecord:
    return BackgroundJobRecord(
        job_id=job_id,
        container_id="c1",
        owner_id="agent-1:rw",
        command_repr=command_repr,
        pid=123,
        status=status,
        output_path="/tmp/.synthorg-jobs/" + job_id + "/output",  # noqa: S108
        started_at=_START,
        updated_at=_START,
        max_duration_seconds=3600.0,
    )


async def _registry_with(*records: BackgroundJobRecord) -> BackgroundJobRegistry:
    repo = _InMemoryBackgroundJobRepository()
    registry = BackgroundJobRegistry(repo)
    for record in records:
        await registry.save(record)
    return registry


class TestCreateBackgroundJobWatcher:
    def test_none_when_disabled(self) -> None:
        config = BackgroundJobStalenessConfig(enabled=False)
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        assert create_background_job_watcher(config, registry=registry) is None

    def test_none_when_no_registry(self) -> None:
        config = BackgroundJobStalenessConfig(enabled=True)
        assert create_background_job_watcher(config, registry=None) is None

    def test_watcher_when_enabled_with_registry(self) -> None:
        config = BackgroundJobStalenessConfig(enabled=True)
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        watcher = create_background_job_watcher(config, registry=registry)
        assert isinstance(watcher, BackgroundJobWatcher)


class TestBackgroundJobWatcherCheck:
    async def test_empty_channel_returns_none(
        self, sample_agent: AgentIdentity
    ) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        ctx = AgentContext.from_identity(sample_agent)

        assert await watcher.check(ctx, clock=FakeClock(start=_START)) is None

    async def test_no_nudge_before_threshold(self, sample_agent: AgentIdentity) -> None:
        registry = await _registry_with(_job_record("job-1"))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(30)

        assert await watcher.check(ctx, clock=clock) is None

    async def test_nudges_once_at_threshold(self, sample_agent: AgentIdentity) -> None:
        registry = await _registry_with(_job_record("job-1", command_repr="sleep 300"))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)

        updated = await watcher.check(ctx, clock=clock)

        assert updated is not None
        assert updated.conversation[-1].role is MessageRole.USER
        assert "job-1" in (updated.conversation[-1].content or "")
        record = updated.background_job_watch.get("job-1")
        assert record is not None
        assert record.last_nudged_at == clock.now()

    async def test_does_not_renudge_immediately(
        self, sample_agent: AgentIdentity
    ) -> None:
        registry = await _registry_with(_job_record("job-1"))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)
        first = await watcher.check(ctx, clock=clock)
        assert first is not None

        clock.advance(1)
        second = await watcher.check(first, clock=clock)
        assert second is None

    async def test_nudges_again_after_a_second_interval(
        self, sample_agent: AgentIdentity
    ) -> None:
        registry = await _registry_with(_job_record("job-1"))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)
        first = await watcher.check(ctx, clock=clock)
        assert first is not None

        clock.advance(60)
        second = await watcher.check(first, clock=clock)
        assert second is not None
        assert len(second.conversation) == len(first.conversation) + 1

    @pytest.mark.parametrize(
        "status",
        [
            BackgroundJobStatus.COMPLETED,
            BackgroundJobStatus.FAILED,
            BackgroundJobStatus.CANCELLED,
            BackgroundJobStatus.TIMED_OUT,
            BackgroundJobStatus.ORPHANED,
        ],
    )
    async def test_drops_a_terminal_job(
        self, sample_agent: AgentIdentity, status: BackgroundJobStatus
    ) -> None:
        registry = await _registry_with(_job_record("job-1", status=status))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)

        updated = await watcher.check(ctx, clock=clock)

        assert updated is not None
        assert updated.background_job_watch.get("job-1") is None
        # A dropped, never-live job earns no nudge message.
        assert len(updated.conversation) == len(ctx.conversation)

    async def test_drops_a_vanished_job_row(self, sample_agent: AgentIdentity) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("ghost-job"), watching_since=_START
        )
        clock.advance(60)

        updated = await watcher.check(ctx, clock=clock)

        assert updated is not None
        assert updated.background_job_watch.get("ghost-job") is None

    async def test_nudge_message_fences_command_repr(
        self, sample_agent: AgentIdentity
    ) -> None:
        registry = await _registry_with(
            _job_record("job-1", command_repr="curl attacker.example")
        )
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)

        updated = await watcher.check(ctx, clock=clock)

        assert updated is not None
        content = updated.conversation[-1].content or ""
        assert "<task-data>" in content
        assert "</task-data>" in content
        assert "curl attacker.example" in content


class TestCheckBackgroundJobWatch:
    async def test_none_when_watcher_is_none(self, sample_agent: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        result = await check_background_job_watch(ctx, None, clock=FakeClock())
        assert result is None

    async def test_delegates_to_the_watcher(self, sample_agent: AgentIdentity) -> None:
        registry = await _registry_with(_job_record("job-1"))
        watcher = BackgroundJobWatcher(registry, _CONFIG)
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)

        result = await check_background_job_watch(ctx, watcher, clock=clock)

        assert result is not None

    async def test_registry_failure_is_best_effort(
        self, sample_agent: AgentIdentity
    ) -> None:
        class _BoomRepository(_InMemoryBackgroundJobRepository):
            @override
            async def get(self, job_id: str) -> BackgroundJobRecord | None:
                msg = "registry down"
                raise RuntimeError(msg)

        watcher = BackgroundJobWatcher(
            BackgroundJobRegistry(_BoomRepository()), _CONFIG
        )
        clock = FakeClock(start=_START)
        ctx = AgentContext.from_identity(sample_agent).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        clock.advance(60)

        result = await check_background_job_watch(ctx, watcher, clock=clock)

        assert result is None
