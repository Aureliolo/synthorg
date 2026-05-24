"""Tests for the telemetry signal aggregator."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from synthorg.telemetry.event_counter import InMemoryTelemetryEventCounter
from synthorg.telemetry.protocol import TelemetryEvent


def _event(event_type: str, ts: datetime) -> TelemetryEvent:
    return TelemetryEvent(
        event_type=NotBlankStr(event_type),
        deployment_id=NotBlankStr("dep"),
        synthorg_version=NotBlankStr("0.0.0"),
        python_version=NotBlankStr("3.14"),
        os_platform=NotBlankStr("linux"),
        timestamp=ts,
    )


@pytest.mark.unit
class TestTelemetrySignalAggregator:
    """Aggregator defers to the counter's summarize method."""

    async def test_no_counter_yields_empty(self) -> None:
        agg = TelemetrySignalAggregator()
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.event_count == 0

    async def test_domain_is_telemetry(self) -> None:
        agg = TelemetrySignalAggregator()
        assert agg.domain == "telemetry"

    async def test_queries_real_counter(self) -> None:
        counter = InMemoryTelemetryEventCounter()
        now = datetime.now(UTC)
        counter.on_event(_event("deployment.startup", now - timedelta(minutes=5)))
        agg = TelemetrySignalAggregator(counter)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now + timedelta(minutes=1),
        )
        assert summary.event_count == 1

    async def test_swallows_counter_errors_returns_empty(self) -> None:
        class _ExplodingCounter:
            def on_event(self, event: TelemetryEvent) -> None:
                return None

            async def summarize(self, **_kwargs: object) -> object:
                msg = "boom"
                raise RuntimeError(msg)

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = TelemetrySignalAggregator(_ExplodingCounter())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.event_count == 0

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """``MemoryError`` / ``RecursionError`` from the counter propagate.

        The aggregator's broad ``except Exception`` returns an empty
        summary, but ``MemoryError`` and ``RecursionError`` MUST escape
        the ``Exception`` net (per CLAUDE.md ``## Logging`` discipline)
        so the surrounding TaskGroup / event loop sees the catastrophic
        interpreter state instead of degrading silently.
        """

        class _CatastrophicCounter:
            def on_event(self, event: TelemetryEvent) -> None:
                return None

            async def summarize(self, **_kwargs: object) -> object:
                raise exc_cls

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = TelemetrySignalAggregator(_CatastrophicCounter())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        with pytest.raises(exc_cls):
            await agg.aggregate(
                since=now - timedelta(hours=1),
                until=now,
            )
