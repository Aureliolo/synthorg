"""Parity guard: feature manifests claim exactly the expected controller set.

Route registration is driven by discovery over feature manifests. Every
controller in the expected set must be claimed by exactly one manifest, and no
manifest may claim a controller outside it. A controller that slips out of a
manifest (404 regression) or a typo'd extra entry fails here.
"""

import pytest
from litestar import Controller
from litestar.handlers import WebsocketRouteHandler

from synthorg._core.features import ControllerRegistration, discover_features
from synthorg._demo.controller import DemoController
from synthorg.a2a.gateway import A2AGatewayController
from synthorg.a2a.well_known import WellKnownAgentCardController
from synthorg.api.controllers import ALL_CONTROLLERS, ws_handler

pytestmark = pytest.mark.unit


def _discovered_controllers() -> set[type[Controller]]:
    """Flatten every manifest's controllers, unwrapping registrations."""
    discovered: set[type[Controller]] = set()
    for feature in discover_features():
        for entry in feature.controllers:
            controller = (
                entry.controller if isinstance(entry, ControllerRegistration) else entry
            )
            discovered.add(controller)
    return discovered


def _discovered_websocket_handlers() -> set[WebsocketRouteHandler]:
    """Collect every manifest's websocket handlers."""
    handlers: set[WebsocketRouteHandler] = set()
    for feature in discover_features():
        handlers.update(feature.websocket_handlers)
    return handlers


def _expected_controllers() -> set[type[Controller]]:
    """The complete expected controller set: base + integration + optional + a2a.

    ``ALL_CONTROLLERS`` already unions ``BASE_CONTROLLERS``,
    ``INTEGRATION_CONTROLLERS``, and the ``OPTIONAL_CONTROLLERS`` classes.
    The two a2a controllers are registered via ``src/synthorg/a2a/feature.py``
    (``ControllerRegistration``) and are absent from ``ALL_CONTROLLERS``, so
    they are added explicitly here. ``DemoController`` is the synthetic
    ``_demo`` feature's discovery guard, so it joins the expected set rather
    than tripping the extra-controller assertion.

    Returns:
        The full set of controller classes the boot path mounts, plus the demo
        discovery guard.
    """
    return {
        *ALL_CONTROLLERS,
        WellKnownAgentCardController,
        A2AGatewayController,
        DemoController,
    }


def test_manifests_claim_exactly_the_expected_controllers() -> None:
    discovered = _discovered_controllers()
    expected = _expected_controllers()
    unclaimed = expected - discovered
    extra = discovered - expected
    assert not unclaimed, f"controllers missing from feature manifests: {unclaimed}"
    assert not extra, f"controllers claimed but absent from expected set: {extra}"


def test_websocket_handler_is_claimed() -> None:
    assert ws_handler in _discovered_websocket_handlers()
