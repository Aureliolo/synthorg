"""Tests for composite scaling trigger."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.hr.scaling.triggers.batched import BatchedScalingTrigger
from synthorg.hr.scaling.triggers.composite import CompositeScalingTrigger
from synthorg.hr.scaling.triggers.threshold import SignalThresholdTrigger

_NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
class TestCompositeScalingTrigger:
    """CompositeScalingTrigger OR combination."""

    @pytest.mark.parametrize(
        ("consume_batched", "signal_initial", "signal_final", "expect_trigger"),
        [
            (False, None, None, True),
            (True, 0.80, 0.90, True),
            (True, None, None, False),
        ],
        ids=[
            "fires-when-first-child-fires",
            "fires-when-second-child-fires",
            "does-not-fire-when-no-child-fires",
        ],
    )
    async def test_composite_trigger_logic(
        self,
        consume_batched: bool,
        signal_initial: float | None,
        signal_final: float | None,
        expect_trigger: bool,
    ) -> None:
        batched = BatchedScalingTrigger(interval_seconds=900)
        if consume_batched:
            await batched.should_trigger()

        threshold = SignalThresholdTrigger(
            signal_name=NotBlankStr("utilization"),
            threshold=0.85,
        )
        if signal_initial is not None:
            await threshold.update_signal(
                ScalingSignal(
                    name=NotBlankStr("utilization"),
                    value=signal_initial,
                    source=NotBlankStr("test"),
                    timestamp=_NOW,
                ),
            )
        if signal_final is not None:
            await threshold.update_signal(
                ScalingSignal(
                    name=NotBlankStr("utilization"),
                    value=signal_final,
                    source=NotBlankStr("test"),
                    timestamp=_NOW,
                ),
            )

        composite = CompositeScalingTrigger(triggers=(batched, threshold))
        assert (await composite.should_trigger()) is expect_trigger

    async def test_update_signal_forwards_to_signal_aware_children(self) -> None:
        threshold = SignalThresholdTrigger(
            signal_name=NotBlankStr("utilization"),
            threshold=0.85,
        )
        batched = BatchedScalingTrigger(interval_seconds=900)
        await batched.should_trigger()
        await batched.record_run()
        composite = CompositeScalingTrigger(triggers=(batched, threshold))

        def _signal(value: float) -> ScalingSignal:
            return ScalingSignal(
                name=NotBlankStr("utilization"),
                value=value,
                source=NotBlankStr("test"),
                timestamp=_NOW,
            )

        # Push through the composite; only the threshold child consumes it.
        await composite.update_signal(_signal(0.80))
        await composite.update_signal(_signal(0.90))
        assert await threshold.should_trigger() is True

    async def test_empty_triggers_returns_false(self) -> None:
        composite = CompositeScalingTrigger(triggers=())
        assert await composite.should_trigger() is False

    async def test_name_property(self) -> None:
        composite = CompositeScalingTrigger(triggers=())
        assert composite.name == "composite"
