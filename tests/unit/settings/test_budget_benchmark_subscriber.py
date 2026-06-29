"""Tests for ``BudgetBenchmarkProviderSettingsSubscriber``.

A ``budget.benchmark_provider`` / ``model_tier_overrides`` change rebuilds the
cost-dial benchmark provider then reloads runtime services so the engine routing
strategy picks up the swapped provider. Tests assert both calls fire in order on
a watched change, no-op on an unexpected pair, and re-raise on failure.
"""

from unittest.mock import AsyncMock, create_autospec

import pytest

import synthorg.api._benchmark_wiring as benchmark_wiring
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.budget_benchmark_subscriber import (
    BudgetBenchmarkProviderSettingsSubscriber,
)
from synthorg.workers import runtime_builder
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[BudgetBenchmarkProviderSettingsSubscriber, AppState]:
    app_state = make_app_state(config=RootConfig(company_name="test"))
    sub = BudgetBenchmarkProviderSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


def _patch_calls(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    """Patch the rebuild + reload entry points with spec'd async spies."""
    rebuild = create_autospec(benchmark_wiring.rebuild_cost_dial_benchmark_provider)
    reload = create_autospec(runtime_builder.reload_runtime_services)
    monkeypatch.setattr(
        benchmark_wiring, "rebuild_cost_dial_benchmark_provider", rebuild
    )
    monkeypatch.setattr(runtime_builder, "reload_runtime_services", reload)
    return rebuild, reload


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset(
            {
                ("budget", "benchmark_provider"),
                ("budget", "model_tier_overrides"),
            }
        )


class TestRebuild:
    async def test_watched_change_rebuilds_then_reloads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rebuild, reload = _patch_calls(monkeypatch)
        calls: list[str] = []

        async def _record_rebuild(*_args: object, **_kwargs: object) -> None:
            calls.append("rebuild")

        async def _record_reload(*_args: object, **_kwargs: object) -> None:
            calls.append("reload")

        rebuild.side_effect = _record_rebuild
        reload.side_effect = _record_reload
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed("budget", "benchmark_provider")
        rebuild.assert_awaited_once_with(app_state)
        reload.assert_awaited_once_with(app_state)
        # The reload rebuilds the engine routing strategy against the slice
        # provider, so the rebuild MUST land first; assert the sequence, not
        # just that both fired.
        assert calls == ["rebuild", "reload"]

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rebuild, reload = _patch_calls(monkeypatch)
        sub, _ = _make_subscriber()
        await sub.on_settings_changed("budget", "unrelated")
        rebuild.assert_not_awaited()
        reload.assert_not_awaited()

    async def test_rebuild_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rebuild, _ = _patch_calls(monkeypatch)
        rebuild.side_effect = RuntimeError("provider boom")
        sub, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="provider boom"):
            await sub.on_settings_changed("budget", "model_tier_overrides")
