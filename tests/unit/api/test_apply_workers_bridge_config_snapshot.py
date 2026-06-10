"""Tests for the ``_apply_workers_bridge_config_snapshot`` startup helper.

:class:`DistributedDispatcher` reads its publish retry budget from
``app_state.bridge_config.workers`` (via a late-bound provider). At
startup the helper resolves the full ``WorkersBridgeConfig`` from
``ConfigResolver`` and atomically swaps it onto ``AppState``. On
failure the default snapshot -- whose Field defaults equal the
registered ``workers.*`` defaults -- is retained and a single
structured warning is emitted, so a settings-backend hiccup never
perturbs the retry budget.
"""

from typing import Any, cast

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.config_apply import (
    _apply_workers_bridge_config_snapshot,
)
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import WorkersBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_state(*, config_resolver: ConfigResolver | None) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        config_resolver=config_resolver,
    )


def _resolver_returning(snapshot: WorkersBridgeConfig) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_workers_bridge_config.return_value = snapshot
    return cast("ConfigResolver", resolver)


def _resolver_raising(exc: BaseException) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_workers_bridge_config.side_effect = exc
    return cast("ConfigResolver", resolver)


class TestApplyWorkersBridgeConfigSnapshot:
    """Startup snapshot wiring for WorkersBridgeConfig."""

    async def test_no_resolver_keeps_default_snapshot(self) -> None:
        state = _make_state(config_resolver=None)
        await _apply_workers_bridge_config_snapshot(state)
        assert state.bridge_config.workers == WorkersBridgeConfig()

    async def test_happy_path_swaps_snapshot(self) -> None:
        custom = WorkersBridgeConfig(dispatcher_publish_max_attempts=9)
        state = _make_state(config_resolver=_resolver_returning(custom))

        await _apply_workers_bridge_config_snapshot(state)

        assert state.bridge_config.workers is custom
        assert state.bridge_config.workers.dispatcher_publish_max_attempts == 9

    async def test_failure_keeps_default_and_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(
            config_resolver=_resolver_raising(RuntimeError("resolver outage")),
        )

        warnings: list[tuple[str, dict[str, Any]]] = []

        from synthorg.api.lifecycle_helpers import config_apply as mod

        original = mod.logger.warning

        def _capture(event: str, **kwargs: Any) -> None:
            warnings.append((event, kwargs))
            original(event, **kwargs)

        monkeypatch.setattr(mod.logger, "warning", _capture)

        await _apply_workers_bridge_config_snapshot(state)

        assert state.bridge_config.workers == WorkersBridgeConfig()
        assert len(warnings) == 1
        event_name, fields = warnings[0]
        assert event_name == "api.bridge_config.resolve_failed"
        assert fields["bridge"] == "workers"
        assert fields["error_type"] == "RuntimeError"
        assert "error" in fields

    async def test_memory_error_propagates(self) -> None:
        state = _make_state(config_resolver=_resolver_raising(MemoryError()))

        with pytest.raises(MemoryError):
            await _apply_workers_bridge_config_snapshot(state)
