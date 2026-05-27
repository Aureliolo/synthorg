"""Tests for the operator-tunable default scored-feedback wiring.

Covers ``_resolve_client_bridge_config`` and ``_build_default_client``,
asserting that the passing-score, strictness multiplier, and
strictness floor flow from ``ClientBridgeConfig`` into the synthetic
feedback profile attached to default ``AIClient`` instances.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.api.controllers.clients import (
    _build_default_client,
    _resolve_client_bridge_config,
)
from synthorg.client.models import ClientProfile
from synthorg.settings.bridge_configs import ClientBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _profile(strictness: float = 0.5) -> ClientProfile:
    return ClientProfile(
        client_id="client-1",
        name="Test Client",
        persona="A test client",
        expertise_domains=(),
        strictness_level=strictness,
    )


def test_build_default_client_uses_config_passing_score() -> None:
    """Custom passing_score on the config flows into the feedback profile."""
    config = ClientBridgeConfig(scored_feedback_passing_score=0.75)
    client = _build_default_client(_profile(), config)
    assert client._feedback._passing_score == pytest.approx(0.75)  # type: ignore[attr-defined]


def test_build_default_client_applies_strictness_multiplier() -> None:
    """Multiplier scales profile.strictness_level into the feedback weight."""
    config = ClientBridgeConfig(
        scored_feedback_strictness_multiplier=4.0,
        scored_feedback_strictness_floor=0.1,
    )
    client = _build_default_client(_profile(strictness=0.5), config)
    # 0.5 * 4.0 = 2.0; floor (0.1) is the lower bound, not an upper cap.
    assert client._feedback._strictness_multiplier == pytest.approx(2.0)  # type: ignore[attr-defined]


def test_build_default_client_enforces_strictness_floor() -> None:
    """Floor prevents strictness=0 from collapsing the multiplier."""
    config = ClientBridgeConfig(
        scored_feedback_strictness_multiplier=2.0,
        scored_feedback_strictness_floor=0.25,
    )
    client = _build_default_client(_profile(strictness=0.0), config)
    # max(0.25, 0.0 * 2.0) = 0.25
    assert client._feedback._strictness_multiplier == pytest.approx(0.25)  # type: ignore[attr-defined]


def test_build_default_client_none_falls_back_to_defaults() -> None:
    """Calling without ``config`` reproduces historical behaviour."""
    client = _build_default_client(_profile(strictness=0.5))
    # Default: passing_score=0.5, multiplier=2.0, floor=0.1.
    # 0.5 * 2.0 = 1.0; floor (0.1) is below.
    assert client._feedback._passing_score == pytest.approx(0.5)  # type: ignore[attr-defined]
    assert client._feedback._strictness_multiplier == pytest.approx(1.0)  # type: ignore[attr-defined]


async def test_resolve_client_bridge_config_falls_back_when_resolver_missing() -> None:
    """During early bootstrap (no resolver wired), defaults are returned."""
    app_state = make_app_state()
    config = await _resolve_client_bridge_config(app_state)
    assert isinstance(config, ClientBridgeConfig)
    assert config.scored_feedback_passing_score == pytest.approx(0.5)
    assert config.scored_feedback_strictness_multiplier == pytest.approx(2.0)
    assert config.scored_feedback_strictness_floor == pytest.approx(0.1)


async def test_resolve_client_bridge_config_calls_resolver_when_wired() -> None:
    """When the resolver is wired, the bridge-config helper drives the result."""
    expected = ClientBridgeConfig(
        scored_feedback_passing_score=0.7,
        scored_feedback_strictness_multiplier=3.0,
        scored_feedback_strictness_floor=0.2,
    )
    resolver_mock = MagicMock(spec=ConfigResolver)
    # ``MagicMock(spec=ConfigResolver)`` auto-creates an ``AsyncMock``
    # child for the async ``get_client_bridge_config`` method, so
    # setting ``return_value`` on the auto-mock is correct (and the
    # spec stays anchored to the concrete class per the project's
    # mock-spec convention rather than spec'ing to a method object).
    resolver_mock.get_client_bridge_config.return_value = expected
    app_state = make_app_state(config_resolver=resolver_mock)
    result = await _resolve_client_bridge_config(app_state)
    assert result is expected
