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
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.state import AppState
from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.communication.delegation.record_store import DelegationRecordStore
from synthorg.config.schema import RootConfig
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.in_memory_bounds_subscriber import (
    InMemoryBoundsSettingsSubscriber,
)
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
