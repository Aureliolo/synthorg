"""Tests for the ``_apply_api_bridge_config_snapshot`` startup helper.

The activities controller (and any future consumer) reads
``app_state.bridge_config.api.<field>`` for operator-tunable knobs.
At startup the helper resolves the full ``ApiBridgeConfig`` from
``ConfigResolver`` and atomically swaps it onto ``AppState``. On
failure the default snapshot is retained and a single structured
warning is emitted -- the centralised replacement for the per-request
log-once fallback the activities controller used to carry inline.
"""

from typing import Any, cast
from unittest.mock import create_autospec

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.config_apply import (
    _apply_api_bridge_config_snapshot,
)
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import ApiBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_state(*, config_resolver: ConfigResolver | None) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        config_resolver=config_resolver,
    )


def _resolver_returning(snapshot: ApiBridgeConfig) -> ConfigResolver:
    resolver = create_autospec(ConfigResolver, instance=True)
    resolver.get_api_bridge_config.return_value = snapshot
    return cast("ConfigResolver", resolver)


def _resolver_raising(exc: BaseException) -> ConfigResolver:
    resolver = create_autospec(ConfigResolver, instance=True)
    resolver.get_api_bridge_config.side_effect = exc
    return cast("ConfigResolver", resolver)


class TestApplyApiBridgeConfigSnapshot:
    """Startup snapshot wiring for ApiBridgeConfig."""

    async def test_no_resolver_keeps_default_snapshot(self) -> None:
        state = _make_state(config_resolver=None)
        await _apply_api_bridge_config_snapshot(state)
        assert state.bridge_config.api == ApiBridgeConfig()

    async def test_happy_path_swaps_snapshot(self) -> None:
        custom = ApiBridgeConfig(max_lifecycle_events_per_query=25_000)
        state = _make_state(config_resolver=_resolver_returning(custom))

        await _apply_api_bridge_config_snapshot(state)

        assert state.bridge_config.api is custom
        assert state.bridge_config.api.max_lifecycle_events_per_query == 25_000

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

        await _apply_api_bridge_config_snapshot(state)

        assert state.bridge_config.api == ApiBridgeConfig()
        # Single warning emitted with the canonical event name and the
        # redacted error description -- never the raw exception string.
        assert len(warnings) == 1
        event_name, fields = warnings[0]
        assert event_name == "api.bridge_config.resolve_failed"
        assert fields["bridge"] == "api"
        assert fields["error_type"] == "RuntimeError"
        # ``safe_error_description`` redacts message bodies; we just
        # confirm the field is present (not asserting on its value).
        assert "error" in fields

    async def test_memory_error_propagates(self) -> None:
        state = _make_state(config_resolver=_resolver_raising(MemoryError()))

        with pytest.raises(MemoryError):
            await _apply_api_bridge_config_snapshot(state)
