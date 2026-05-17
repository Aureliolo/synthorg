"""Tests for ``WorkersBridgeSettingsSubscriber``.

The subscriber hot-swaps ``app_state.workers_bridge_config`` when the
operator changes a watched ``workers.dispatcher_publish_*`` setting.
Tests cover protocol conformance, happy-path swap for the int knob
(``get_int``) and a float knob (``get_float``) with every other field
preserved, unexpected key/namespace no-op, resolver-failure (no swap,
re-raised), out-of-range rejection retaining the prior snapshot, and
``MemoryError`` propagation.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import WorkersBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.workers_bridge_subscriber import (
    WorkersBridgeSettingsSubscriber,
)

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    snapshot: WorkersBridgeConfig | None = None,
    int_return: int | None = None,
    float_return: float | None = None,
    int_side_effect: BaseException | None = None,
    float_side_effect: BaseException | None = None,
) -> tuple[WorkersBridgeSettingsSubscriber, AppState]:
    """Build a subscriber with a real AppState + spec'd ConfigResolver."""
    settings_service = create_autospec(SettingsService, instance=True)

    resolver = create_autospec(ConfigResolver, instance=True)
    if int_side_effect is not None:
        resolver.get_int.side_effect = int_side_effect
    else:
        resolver.get_int.return_value = int_return
    if float_side_effect is not None:
        resolver.get_float.side_effect = float_side_effect
    else:
        resolver.get_float.return_value = float_return

    app_state = AppState(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
    )
    app_state._config_resolver = resolver
    if snapshot is not None:
        app_state.swap_workers_bridge_config(snapshot)

    sub = WorkersBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    return sub, app_state


class TestSubscriberProtocol:
    """``WorkersBridgeSettingsSubscriber`` conforms to ``SettingsSubscriber``."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber(int_return=3)
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber(int_return=3)
        assert sub.watched_keys == frozenset(
            {
                ("workers", "dispatcher_publish_max_attempts"),
                ("workers", "dispatcher_publish_backoff_base_seconds"),
                ("workers", "dispatcher_publish_backoff_cap_seconds"),
            }
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber(int_return=3)
        assert sub.subscriber_name == "workers-bridge-config"


class TestRebuild:
    """``on_settings_changed`` rebuilds + swaps the snapshot."""

    async def test_int_knob_change_swaps_via_get_int(self) -> None:
        original = WorkersBridgeConfig(
            dispatcher_publish_max_attempts=3,
            dispatcher_publish_backoff_base_seconds=0.25,
        )
        sub, app_state = _make_subscriber(snapshot=original, int_return=7)

        await sub.on_settings_changed("workers", "dispatcher_publish_max_attempts")

        swapped = app_state.workers_bridge_config
        assert swapped.dispatcher_publish_max_attempts == 7
        # Every other field is preserved verbatim from the prior snapshot.
        assert swapped.dispatcher_publish_backoff_base_seconds == 0.25

    async def test_float_knob_change_swaps_via_get_float(self) -> None:
        original = WorkersBridgeConfig(dispatcher_publish_max_attempts=5)
        sub, app_state = _make_subscriber(snapshot=original, float_return=2.5)

        await sub.on_settings_changed(
            "workers", "dispatcher_publish_backoff_base_seconds"
        )

        swapped = app_state.workers_bridge_config
        assert swapped.dispatcher_publish_backoff_base_seconds == 2.5
        assert swapped.dispatcher_publish_max_attempts == 5

    async def test_resolver_failure_does_not_swap(self) -> None:
        original = WorkersBridgeConfig(dispatcher_publish_max_attempts=4)
        sub, app_state = _make_subscriber(
            snapshot=original,
            int_side_effect=RuntimeError("resolver outage"),
        )

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("workers", "dispatcher_publish_max_attempts")

        assert app_state.workers_bridge_config is original
        assert app_state.workers_bridge_config.dispatcher_publish_max_attempts == 4

    async def test_memory_error_propagates(self) -> None:
        sub, app_state = _make_subscriber(int_side_effect=MemoryError())
        before = app_state.workers_bridge_config

        with pytest.raises(MemoryError):
            await sub.on_settings_changed("workers", "dispatcher_publish_max_attempts")

        assert app_state.workers_bridge_config is before

    async def test_out_of_range_value_rejected_keeps_prior_snapshot(self) -> None:
        # ``dispatcher_publish_max_attempts`` is bounded ``ge=1, le=10``.
        # ``mutate_workers_bridge_config`` re-validates, so an operator
        # value above the bound must raise ``ValidationError`` -- the
        # subscriber logs + re-raises and the prior snapshot stays.
        original = WorkersBridgeConfig(dispatcher_publish_max_attempts=3)
        sub, app_state = _make_subscriber(
            snapshot=original,
            int_return=99,  # above the le=10 bound
        )

        with pytest.raises(Exception) as exc_info:  # noqa: PT011 -- pydantic
            await sub.on_settings_changed("workers", "dispatcher_publish_max_attempts")
        assert exc_info.type.__name__ == "ValidationError"

        assert app_state.workers_bridge_config is original
        assert app_state.workers_bridge_config.dispatcher_publish_max_attempts == 3


class TestUnexpectedRouting:
    """Unexpected (namespace, key) pairs are logged and no-op."""

    async def test_unknown_namespace_is_ignored(self) -> None:
        original = WorkersBridgeConfig(dispatcher_publish_max_attempts=6)
        sub, app_state = _make_subscriber(snapshot=original, int_return=9)

        await sub.on_settings_changed("other", "dispatcher_publish_max_attempts")

        assert app_state.workers_bridge_config is original

    async def test_unknown_key_is_ignored(self) -> None:
        original = WorkersBridgeConfig(dispatcher_publish_max_attempts=6)
        sub, app_state = _make_subscriber(snapshot=original, int_return=9)

        await sub.on_settings_changed("workers", "some_unrelated_key")

        assert app_state.workers_bridge_config is original
