"""Unit tests for the meeting-driven strategic context provider.

Covers the alignment-qualifier matrix, fallback paths, the MEETING
``build_context`` branch, the cached ambient provider, and the prompt
section read-through.
"""

from collections.abc import Iterator

import pytest

from synthorg.engine.strategy.context import (
    ConfigContextProvider,
    MeetingContextProvider,
    build_context,
)
from synthorg.engine.strategy.models import (
    ContextSource,
    StrategicContextConfig,
    StrategyConfig,
)
from synthorg.engine.strategy.prompt_injection import (
    build_strategic_prompt_sections,
)
from synthorg.engine.strategy.strategic_context_provider import (
    CachedStrategicContextProvider,
    current_strategic_context,
    set_strategic_context_provider,
)
from synthorg.hr.seniority import SeniorityLevel

from .conftest import make_agent

pytestmark = pytest.mark.unit


class _FakeMinutes:
    def __init__(self, *, decisions: tuple[str, ...], conflicts: bool) -> None:
        self.decisions = decisions
        self.conflicts_detected = conflicts


class _FakeRecord:
    def __init__(self, minutes: _FakeMinutes | None) -> None:
        self.minutes = minutes


class _FakeRecordsSource:
    def __init__(self, records: tuple[_FakeRecord, ...]) -> None:
        self._records = records

    def get_records(self) -> tuple[_FakeRecord, ...]:
        return self._records


def _records_source(records: tuple[_FakeRecord, ...]) -> _FakeRecordsSource:
    return _FakeRecordsSource(records)


def _aligned_record() -> _FakeRecord:
    return _FakeRecord(_FakeMinutes(decisions=("ship",), conflicts=False))


def _contested_record() -> _FakeRecord:
    return _FakeRecord(_FakeMinutes(decisions=("ship",), conflicts=True))


def _config(source: ContextSource = ContextSource.MEETING) -> StrategyConfig:
    return StrategyConfig(context=StrategicContextConfig(source=source))


@pytest.fixture
def _reset_ambient() -> Iterator[None]:
    set_strategic_context_provider(None)
    yield
    set_strategic_context_provider(None)


class TestMeetingContextProvider:
    async def test_aligned_qualifier(self) -> None:
        source = _records_source((_aligned_record(),) * 3)
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(), records_source=source, lookback=5
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "aligned challenger"

    async def test_contested_qualifier(self) -> None:
        source = _records_source((_contested_record(),) * 3)
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(), records_source=source, lookback=5
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "contested challenger"

    async def test_indeterminate_band_no_override(self) -> None:
        # 2 aligned / 4 = 0.5 ratio, between the low (0.3) and high (0.6) bands.
        records = (
            _aligned_record(),
            _aligned_record(),
            _contested_record(),
            _contested_record(),
        )
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(),
            records_source=_records_source(records),
            lookback=5,
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "challenger"

    async def test_no_records_source_falls_back(self) -> None:
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(), records_source=None, lookback=5
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "challenger"

    async def test_no_completed_meetings_falls_back(self) -> None:
        scheduled = _FakeRecord(None)
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(),
            records_source=_records_source((scheduled, scheduled)),
            lookback=5,
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "challenger"

    async def test_lookback_limits_window(self) -> None:
        # Older contested meetings beyond the lookback are ignored; the
        # 2 most recent are aligned -> aligned.
        records = (
            _contested_record(),
            _contested_record(),
            _contested_record(),
            _aligned_record(),
            _aligned_record(),
        )
        provider = MeetingContextProvider(
            fallback=ConfigContextProvider(),
            records_source=_records_source(records),
            lookback=2,
        )
        ctx = await provider.provide(config=_config())
        assert ctx.competitive_position == "aligned challenger"


class TestBuildContextMeetingSource:
    async def test_meeting_source_uses_provider(self) -> None:
        source = _records_source((_aligned_record(),) * 2)
        ctx = await build_context(_config(), meeting_records=source)
        assert ctx.competitive_position == "aligned challenger"

    async def test_meeting_source_without_records_degrades(self) -> None:
        ctx = await build_context(_config(), meeting_records=None)
        assert ctx.competitive_position == "challenger"


class TestCachedStrategicContextProvider:
    async def test_refresh_then_current(self) -> None:
        source = _records_source((_aligned_record(),) * 2)

        async def _resolve() -> object:
            return await build_context(_config(), meeting_records=source)

        provider = CachedStrategicContextProvider(resolver=_resolve)  # type: ignore[arg-type]
        assert provider.current() is None
        await provider.refresh()
        snapshot = provider.current()
        assert snapshot is not None
        assert snapshot.competitive_position == "aligned challenger"

    @pytest.mark.usefixtures("_reset_ambient")
    async def test_ambient_holder(self) -> None:
        assert current_strategic_context() is None
        source = _records_source((_aligned_record(),) * 2)

        async def _resolve() -> object:
            return await build_context(_config(), meeting_records=source)

        provider = CachedStrategicContextProvider(resolver=_resolve)  # type: ignore[arg-type]
        await provider.refresh()
        set_strategic_context_provider(provider)
        ambient = current_strategic_context()
        assert ambient is not None
        assert ambient.competitive_position == "aligned challenger"

    @pytest.mark.usefixtures("_reset_ambient")
    async def test_prompt_sections_read_ambient_context(self) -> None:
        source = _records_source((_aligned_record(),) * 2)

        async def _resolve() -> object:
            return await build_context(_config(), meeting_records=source)

        provider = CachedStrategicContextProvider(resolver=_resolve)  # type: ignore[arg-type]
        await provider.refresh()
        set_strategic_context_provider(provider)
        agent = make_agent(level=SeniorityLevel.C_SUITE)
        sections = build_strategic_prompt_sections(config=StrategyConfig(), agent=agent)
        assert "aligned challenger" in str(sections["strategic_context_text"])
