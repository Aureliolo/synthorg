"""Startup hands the started health prober back to provider management.

The prober is built during on-startup wiring, after the management service
already exists, so probe-on-mutation only functions if startup completes the
hand-off. Without it a freshly created provider reports UNKNOWN (rendered
identically to unreachable) until the next periodic sweep, which is the exact
regression these tests exist to catch.
"""

from collections.abc import Mapping
from unittest.mock import patch

import pytest

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.lifecycle import _maybe_start_health_prober
from synthorg.api.state import AppState
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(management: object | None) -> AppState:
    """Build the minimum state the prober startup helper requires."""
    slices: Mapping[type[BaseFeatureStateSlice], Mapping[str, object]] | None = (
        {ProvidersStateSlice: {"management": management}}
        if management is not None
        else None
    )
    return make_app_state(
        provider_health_tracker=ProviderHealthTracker(),
        config_resolver=mock_of[ConfigResolver](),
        slices=slices,
    )


class TestHealthProberStartupWiring:
    async def test_started_prober_is_handed_to_management(self) -> None:
        management = mock_of[ProviderManagementService]()
        # Patched so the assertion is about the hand-off alone and no
        # background sweep task outlives the test.
        with patch.object(ProviderHealthProber, "start", autospec=True) as start:
            prober = await _maybe_start_health_prober(_app_state(management))

        assert prober is not None
        start.assert_awaited_once()
        management.set_probe_requester.assert_called_once_with(prober)

    async def test_failed_start_leaves_management_unwired(self) -> None:
        """An unstarted prober must not be handed out as a probe requester."""
        management = mock_of[ProviderManagementService]()
        with patch.object(ProviderHealthProber, "start", autospec=True) as start:
            start.side_effect = RuntimeError("prober start exploded")
            prober = await _maybe_start_health_prober(_app_state(management))

        assert prober is None
        management.set_probe_requester.assert_not_called()

    async def test_starts_without_management(self) -> None:
        """Periodic probing still runs when the management service is absent."""
        with patch.object(ProviderHealthProber, "start", autospec=True) as start:
            prober = await _maybe_start_health_prober(_app_state(None))

        assert prober is not None
        start.assert_awaited_once()

    async def test_skipped_without_a_health_tracker(self) -> None:
        app_state = make_app_state(config_resolver=mock_of[ConfigResolver]())

        assert await _maybe_start_health_prober(app_state) is None
