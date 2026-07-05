"""Tests for ``DirectMcpActorSettingsSubscriber``.

The subscriber rebuilds the direct-MCP conversational actor on a live
``chief_of_staff.direct_mcp_enabled`` toggle. The security-relevant behaviour
is that the rebuild goes through the fail-closed builder: with no boot engine
(hence no security governance / MCP self-consumer) the actor stays ``None`` and
a cross-warning names the missing prerequisite, so a live enable can never
expose ungated acting.
"""

from types import SimpleNamespace

import pytest
import structlog

from synthorg.api.state import AppState
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.direct_mcp_actor_subscriber import (
    DirectMcpActorSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_KEY = ("chief_of_staff", "direct_mcp_enabled")


def _make_subscriber() -> tuple[DirectMcpActorSettingsSubscriber, AppState]:
    service = mock_of[SettingsService]()
    app_state = make_app_state(settings_service=service)
    sub = DirectMcpActorSettingsSubscriber(
        app_state=app_state,
        settings_service=service,
    )
    return sub, app_state


def _si_config(*, direct_mcp_enabled: bool) -> SelfImprovementConfig:
    return SelfImprovementConfig(
        chief_of_staff=ChiefOfStaffConfig(direct_mcp_enabled=direct_mcp_enabled),
    )


def _patch_loader(monkeypatch: pytest.MonkeyPatch, *, direct_mcp_enabled: bool) -> None:
    """Patch the config loader the subscriber imports to a fixed config."""

    async def _loader(_service: object) -> SelfImprovementConfig:
        return _si_config(direct_mcp_enabled=direct_mcp_enabled)

    monkeypatch.setattr("synthorg.meta.config.load_self_improvement_config", _loader)


def test_conforms_and_watches_only_direct_mcp() -> None:
    sub, _ = _make_subscriber()
    assert isinstance(sub, SettingsSubscriber)
    assert sub.watched_keys == frozenset({_KEY})
    assert sub.subscriber_name == "direct-mcp-actor-settings"


async def test_enabled_without_engine_stays_none_and_cross_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling with no boot engine leaves the actor None (fail-closed)."""
    sub, app_state = _make_subscriber()
    _patch_loader(monkeypatch, direct_mcp_enabled=True)
    with structlog.testing.capture_logs() as logs:
        await sub.on_settings_changed(*_KEY)
    assert app_state.slice(MetaStateSlice).conversational_actor is None
    warnings = [
        log
        for log in logs
        if log["log_level"] == "warning" and "inert" in log.get("note", "")
    ]
    assert warnings, f"expected an inert cross-warning, got {logs}"


async def test_disabled_tears_the_actor_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A toggle-off swaps a previously-wired actor back to None."""
    sub, app_state = _make_subscriber()
    app_state.wire(MetaStateSlice, conversational_actor=SimpleNamespace())
    _patch_loader(monkeypatch, direct_mcp_enabled=False)
    await sub.on_settings_changed(*_KEY)
    assert app_state.slice(MetaStateSlice).conversational_actor is None
