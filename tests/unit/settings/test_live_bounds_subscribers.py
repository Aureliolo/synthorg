"""Tests for the subscribers that make the newly-live settings live.

Each of these keys used to need a restart. The test that matters for the
invariant is therefore end-to-end through the real settings service: write
the setting, let the subscriber run, and assert the running object changed
with nothing rebuilt. The refusal cases matter just as much, because a
subscriber that swallowed a bad value would leave the operator looking at a
dashboard that disagrees with what is enforcing.
"""

from collections import deque
from collections.abc import AsyncIterator, Mapping
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.config.compression import CompressionConfig

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.state import AppState
from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.communication.delegation.record_store import DelegationRecordStore
from synthorg.config.rate_limits import LiveRateLimits
from synthorg.config.schema import RootConfig
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.engine.flight_recording.sink import LiveFlightRecorderSink
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.compression_subscriber import (
    CompressionSettingsSubscriber,
)
from synthorg.settings.subscribers.flight_recorder_subscriber import (
    FlightRecorderSettingsSubscriber,
)
from synthorg.settings.subscribers.global_rate_limit_subscriber import (
    GlobalRateLimitSettingsSubscriber,
)
from synthorg.settings.subscribers.in_memory_bounds_subscriber import (
    InMemoryBoundsSettingsSubscriber,
)
from synthorg.settings.subscribers.telemetry_subscriber import (
    TelemetrySettingsSubscriber,
)
from synthorg.telemetry.collector import TelemetryCollector
from synthorg.telemetry.state import TelemetryStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

type SliceWiring = Mapping[type[BaseFeatureStateSlice], Mapping[str, object]]


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    """A settings service over a connected in-memory backend."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _resolver(settings: SettingsService) -> ConfigResolver:
    return ConfigResolver(
        settings_service=settings, config=RootConfig(company_name="test")
    )


def _app_state(
    settings: SettingsService,
    *,
    slices: SliceWiring | None = None,
    resolver: object | None = None,
    **wiring: object,
) -> AppState:
    return make_app_state(
        settings_service=settings,
        config_resolver=resolver if resolver is not None else _resolver(settings),
        slices=slices,
        **wiring,
    )


def _canned_settings(values: dict[tuple[str, str], str]) -> SettingsService:
    """A settings service returning fixed raw values, bypassing validation.

    The write path rejects most of these values, so a subscriber's own
    validation can only be reached by feeding it what a hand-edited row or a
    future definition change could still put in front of it.
    """

    async def _get(namespace: str, key: str) -> SimpleNamespace:
        return SimpleNamespace(value=values.get((namespace, key), ""))

    canned: SettingsService = mock_of[SettingsService](get=AsyncMock(side_effect=_get))
    return canned


def _bound_of(store: object) -> int | None:
    """Read a deque-backed store's live bound.

    The bound is the deque's ``maxlen``: there is no public reader because
    the value an operator sees comes from the settings API, not the buffer.
    """
    records: deque[object] = store._records  # type: ignore[attr-defined]
    return records.maxlen


class TestInMemoryBounds:
    """The four in-memory bounds rebound the live buffer, keeping records."""

    @staticmethod
    def _stores() -> tuple[
        CoordinationMetricsStore, BaselineStore, DelegationRecordStore, MagicMock
    ]:
        return (
            CoordinationMetricsStore(max_entries=5),
            BaselineStore(window_size=5),
            DelegationRecordStore(max_records=5),
            mock_of[TaskEngine](),
        )

    @classmethod
    def _wired(
        cls, settings: SettingsService, *, resolver: object | None = None
    ) -> tuple[
        InMemoryBoundsSettingsSubscriber,
        CoordinationMetricsStore,
        BaselineStore,
        DelegationRecordStore,
        MagicMock,
    ]:
        metrics, baseline, delegation, engine = cls._stores()
        state = _app_state(
            settings,
            resolver=resolver,
            delegation_record_store=delegation,
            task_engine=engine,
            slices={
                CoordinationStateSlice: {
                    "metrics_store": metrics,
                    "baseline_store": baseline,
                }
            },
        )
        return (
            InMemoryBoundsSettingsSubscriber(state, settings),
            metrics,
            baseline,
            delegation,
            engine,
        )

    def test_conforms_to_the_subscriber_protocol(
        self, settings: SettingsService
    ) -> None:
        sub, *_ = self._wired(settings)
        assert isinstance(sub, SettingsSubscriber)
        assert sub.subscriber_name == "in-memory-bounds"

    async def test_metrics_bound_is_applied_live(
        self, settings: SettingsService
    ) -> None:
        sub, metrics, *_ = self._wired(settings)
        await settings.set("budget", "coordination_metrics_max_entries", "9")

        await sub.on_settings_changed("budget", "coordination_metrics_max_entries")

        assert _bound_of(metrics) == 9

    async def test_baseline_window_is_applied_live(
        self, settings: SettingsService
    ) -> None:
        sub, _metrics, baseline, *_ = self._wired(settings)
        await settings.set("budget", "baseline_window_size", "12")

        await sub.on_settings_changed("budget", "baseline_window_size")

        assert _bound_of(baseline) == 12

    async def test_delegation_bound_is_applied_live(
        self, settings: SettingsService
    ) -> None:
        sub, _metrics, _baseline, delegation, _engine = self._wired(settings)
        # The definition floors this at 100, so the change has to clear it.
        await settings.set("communication", "delegation_record_store_max_size", "150")

        await sub.on_settings_changed(
            "communication", "delegation_record_store_max_size"
        )

        assert _bound_of(delegation) == 150

    async def test_task_engine_cap_is_applied_live(
        self, settings: SettingsService
    ) -> None:
        sub, _metrics, _baseline, _delegation, engine = self._wired(settings)
        await settings.set("engine", "task_engine_max_queue_size", "42")

        await sub.on_settings_changed("engine", "task_engine_max_queue_size")

        engine.set_max_queue_size.assert_called_once_with(42)

    async def test_an_unexpected_pair_is_ignored(
        self, settings: SettingsService
    ) -> None:
        sub, metrics, *_ = self._wired(settings)
        await sub.on_settings_changed("budget", "some_unrelated_key")
        assert _bound_of(metrics) == 5

    async def test_an_unwired_buffer_is_not_an_error(
        self, settings: SettingsService
    ) -> None:
        # A bound can be written before the buffer it governs exists; the
        # next reconcile pass builds it from the stored value.
        sub = InMemoryBoundsSettingsSubscriber(_app_state(settings), settings)
        await settings.set("budget", "coordination_metrics_max_entries", "9")
        await sub.on_settings_changed("budget", "coordination_metrics_max_entries")

    async def test_a_rejected_bound_leaves_the_buffer_alone(
        self, settings: SettingsService
    ) -> None:
        # The store refuses a bound it could never have been built with, so
        # the previous one keeps evicting rather than a zero-length buffer
        # silently dropping every record.
        resolver = mock_of[ConfigResolver](get_int=AsyncMock(return_value=0))
        sub, metrics, *_ = self._wired(settings, resolver=resolver)
        with pytest.raises(ValueError, match="max_entries"):
            await sub.on_settings_changed("budget", "coordination_metrics_max_entries")
        assert _bound_of(metrics) == 5


class TestGlobalRateLimit:
    """The global tiers rebuild whole, so the cross-tier floor holds."""

    @staticmethod
    def _wired(
        settings: SettingsService, *, reads_through: SettingsService | None = None
    ) -> tuple[GlobalRateLimitSettingsSubscriber, AppState]:
        state = _app_state(settings)
        return (
            GlobalRateLimitSettingsSubscriber(state, reads_through or settings),
            state,
        )

    def test_conforms_to_the_subscriber_protocol(
        self, settings: SettingsService
    ) -> None:
        sub, _ = self._wired(settings)
        assert isinstance(sub, SettingsSubscriber)
        assert sub.subscriber_name == "global-rate-limit-settings"

    async def test_a_cap_change_swaps_the_whole_config(
        self, settings: SettingsService
    ) -> None:
        sub, state = self._wired(settings)
        # The floor has to stay at or above both tiers at every step, so the
        # tier comes down before the floor follows it.
        await settings.set("api", "rate_limit_auth_max_requests", "150")
        await settings.set("api", "rate_limit_floor_max_requests", "600")

        await sub.on_settings_changed("api", "rate_limit_auth_max_requests")

        swapped = state.per_op_limits.global_config
        assert swapped is not None
        assert swapped.auth_max_requests == 150
        assert swapped.floor_max_requests == 600

    async def test_the_window_unit_is_live_too(self, settings: SettingsService) -> None:
        sub, state = self._wired(settings)
        await settings.set("api", "rate_limit_time_unit", "hour")

        await sub.on_settings_changed("api", "rate_limit_time_unit")

        swapped = state.per_op_limits.global_config
        assert swapped is not None
        assert swapped.time_unit == "hour"

    async def test_a_floor_below_a_tier_is_refused_and_nothing_swaps(
        self, settings: SettingsService
    ) -> None:
        # The write path already rejects this pairing, so the only way it
        # reaches the subscriber is a row that got there another way. The
        # rebuild is where that is caught, and the previous config keeps
        # enforcing rather than one that silently caps a tier.
        canned = _canned_settings(
            {
                ("api", "rate_limiter_enabled"): "true",
                ("api", "rate_limit_floor_max_requests"): "100",
                ("api", "rate_limit_unauth_max_requests"): "900",
                ("api", "rate_limit_auth_max_requests"): "900",
                ("api", "rate_limit_auth_endpoint_max_requests"): "10",
                ("api", "rate_limit_time_unit"): "minute",
            }
        )
        sub, state = self._wired(settings, reads_through=canned)
        previous = LiveRateLimits(
            floor_max_requests=500,
            unauth_max_requests=100,
            auth_max_requests=100,
            auth_endpoint_max_requests=10,
        )
        state.per_op_limits.set_global_config(previous)

        with pytest.raises(ValueError, match="would cap a tier"):
            await sub.on_settings_changed("api", "rate_limit_floor_max_requests")

        assert state.per_op_limits.global_config == previous

    async def test_a_malformed_boolean_is_refused_rather_than_disabling(
        self, settings: SettingsService
    ) -> None:
        # Collapsing an unrecognised value to ``False`` would switch the
        # limiter off, which is the one outcome worth refusing outright.
        canned = _canned_settings({("api", "rate_limiter_enabled"): "yes"})
        sub, state = self._wired(settings, reads_through=canned)

        with pytest.raises(ValueError, match="not a valid boolean"):
            await sub.on_settings_changed("api", "rate_limiter_enabled")

        assert state.per_op_limits.global_config is None

    async def test_an_unknown_window_is_refused(
        self, settings: SettingsService
    ) -> None:
        canned = _canned_settings(
            {
                ("api", "rate_limiter_enabled"): "true",
                ("api", "rate_limit_floor_max_requests"): "900",
                ("api", "rate_limit_unauth_max_requests"): "100",
                ("api", "rate_limit_auth_max_requests"): "100",
                ("api", "rate_limit_auth_endpoint_max_requests"): "10",
                ("api", "rate_limit_time_unit"): "fortnight",
            }
        )
        sub, state = self._wired(settings, reads_through=canned)

        with pytest.raises(ValueError, match="not a known window"):
            await sub.on_settings_changed("api", "rate_limit_time_unit")

        assert state.per_op_limits.global_config is None


class TestCompressionThreshold:
    """The compression threshold retunes the config Litestar was built with."""

    @staticmethod
    def _wired(
        settings: SettingsService,
        *,
        with_app: bool = True,
    ) -> tuple[CompressionSettingsSubscriber, AppState]:
        state = _app_state(settings)
        if with_app:
            state.compression.install(CompressionConfig(backend="gzip"))
        return CompressionSettingsSubscriber(state, settings), state

    def test_conforms_to_the_subscriber_protocol(
        self, settings: SettingsService
    ) -> None:
        sub, _ = self._wired(settings)
        assert isinstance(sub, SettingsSubscriber)
        assert sub.subscriber_name == "compression-threshold"

    async def test_the_threshold_is_applied_live(
        self, settings: SettingsService
    ) -> None:
        sub, state = self._wired(settings)
        await settings.set("api", "compression_minimum_size_bytes", "2048")

        await sub.on_settings_changed("api", "compression_minimum_size_bytes")

        assert state.compression.minimum_size == 2048

    async def test_no_app_yet_is_not_an_error(self, settings: SettingsService) -> None:
        sub, state = self._wired(settings, with_app=False)
        await settings.set("api", "compression_minimum_size_bytes", "2048")

        await sub.on_settings_changed("api", "compression_minimum_size_bytes")

        assert state.compression.minimum_size is None

    async def test_an_unexpected_pair_is_ignored(
        self, settings: SettingsService
    ) -> None:
        sub, state = self._wired(settings)
        before = state.compression.minimum_size
        await sub.on_settings_changed("api", "some_unrelated_key")
        assert state.compression.minimum_size == before


class TestFlightRecorder:
    """The recorder configuration re-resolves onto the sink the engine holds."""

    @staticmethod
    def _wired(
        settings: SettingsService,
        *,
        with_sink: bool = True,
    ) -> tuple[FlightRecorderSettingsSubscriber, LiveFlightRecorderSink | None]:
        sink = LiveFlightRecorderSink(lambda: None) if with_sink else None
        slices: SliceWiring = (
            {EngineStateSlice: {"flight_recorder_sink": sink}} if sink else {}
        )
        state = _app_state(settings, slices=slices)
        return FlightRecorderSettingsSubscriber(state, settings), sink

    def test_conforms_to_the_subscriber_protocol(
        self, settings: SettingsService
    ) -> None:
        sub, _ = self._wired(settings)
        assert isinstance(sub, SettingsSubscriber)
        assert sub.subscriber_name == "flight-recorder"

    async def test_a_recorder_change_reaches_the_live_sink(
        self, settings: SettingsService
    ) -> None:
        sub, sink = self._wired(settings)
        assert sink is not None
        await settings.set("cockpit", "flight_recorder_summary_max_chars", "1234")

        await sub.on_settings_changed("cockpit", "flight_recorder_summary_max_chars")

        assert sink.summary_max_chars == 1234

    async def test_no_sink_yet_is_not_an_error(self, settings: SettingsService) -> None:
        sub, _ = self._wired(settings, with_sink=False)
        await sub.on_settings_changed("cockpit", "flight_recorder_enabled")

    async def test_an_unexpected_pair_is_ignored(
        self, settings: SettingsService
    ) -> None:
        sub, sink = self._wired(settings)
        assert sink is not None
        before = sink.summary_max_chars
        await sub.on_settings_changed("cockpit", "some_unrelated_key")
        assert sink.summary_max_chars == before


class TestTelemetryOptIn:
    """The opt-in reaches the resident collector without rebuilding it."""

    @staticmethod
    def _wired(
        settings: SettingsService,
        *,
        with_collector: bool = True,
    ) -> tuple[TelemetrySettingsSubscriber, MagicMock | None]:
        collector = mock_of[TelemetryCollector]() if with_collector else None
        slices: SliceWiring = (
            {TelemetryStateSlice: {"collector": collector}} if collector else {}
        )
        state = _app_state(settings, slices=slices)
        return TelemetrySettingsSubscriber(state, settings), collector

    def test_conforms_to_the_subscriber_protocol(
        self, settings: SettingsService
    ) -> None:
        sub, _ = self._wired(settings)
        assert isinstance(sub, SettingsSubscriber)
        assert sub.subscriber_name == "telemetry-opt-in"

    @pytest.mark.parametrize(("raw", "expected"), [("true", True), ("false", False)])
    async def test_the_opt_in_reaches_the_collector(
        self, settings: SettingsService, raw: str, expected: bool
    ) -> None:
        sub, collector = self._wired(settings)
        assert collector is not None
        await settings.set("telemetry", "enabled", raw)

        await sub.on_settings_changed("telemetry", "enabled")

        collector.apply_resolved_enabled.assert_called_once_with(enabled=expected)

    async def test_no_collector_yet_is_not_an_error(
        self, settings: SettingsService
    ) -> None:
        sub, _ = self._wired(settings, with_collector=False)
        await sub.on_settings_changed("telemetry", "enabled")

    async def test_an_unexpected_pair_is_ignored(
        self, settings: SettingsService
    ) -> None:
        sub, collector = self._wired(settings)
        assert collector is not None
        await sub.on_settings_changed("telemetry", "some_unrelated_key")
        collector.apply_resolved_enabled.assert_not_called()
