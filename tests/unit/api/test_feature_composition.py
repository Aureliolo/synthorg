"""Tests for boot-time feature composition.

Covers :func:`collect_route_handlers`: the discovery-based replacement for
the hand-maintained ``BASE_CONTROLLERS`` / ``INTEGRATION_CONTROLLERS`` /
``OPTIONAL_CONTROLLERS`` lists. The route collector iterates the discovered
feature manifests, normalises bare controllers and registrations, evaluates
mount predicates, and partitions handlers by mount point.
"""

from types import SimpleNamespace
from typing import cast

import pytest
from litestar import Controller
from litestar.handlers import websocket

from synthorg._core.features import ControllerRegistration, FeatureManifest
from synthorg.api import feature_composition
from synthorg.api.feature_composition import collect_route_handlers
from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


class _CtlApi(Controller):
    path = "/api-ctl"


class _CtlRoot(Controller):
    path = "/.well-known/root-ctl"


class _CtlGatedOut(Controller):
    path = "/gated-out"


class _CtlGatedIn(Controller):
    path = "/gated-in"


@websocket("/ws")
async def _ws_handler() -> None: ...  # pragma: no cover - structural only


def _state() -> AppState:
    """Opaque stand-in: the collector only forwards it to predicates."""
    return cast("AppState", SimpleNamespace())


def _patch_discovery(
    monkeypatch: pytest.MonkeyPatch, manifests: tuple[FeatureManifest, ...]
) -> None:
    """Point the route collector at a controlled manifest set."""
    monkeypatch.setattr(feature_composition, "discover_features", lambda: manifests)


def _never(_: object) -> bool:
    return False


def _always(_: object) -> bool:
    return True


def test_bare_controller_mounts_under_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch, (FeatureManifest(name="a", controllers=(_CtlApi,)),))
    api_handlers, root_handlers = collect_route_handlers(_state())
    assert api_handlers == [_CtlApi]
    assert root_handlers == []


def test_registration_mount_root(monkeypatch: pytest.MonkeyPatch) -> None:
    registration = ControllerRegistration(controller=_CtlRoot, mount="root")
    _patch_discovery(
        monkeypatch, (FeatureManifest(name="a", controllers=(registration,)),)
    )
    api_handlers, root_handlers = collect_route_handlers(_state())
    assert api_handlers == []
    assert root_handlers == [_CtlRoot]


def test_predicate_gates_mounting(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = cast("AppState", SimpleNamespace(marker=object()))
    seen: list[object] = []

    def _predicate(state: object) -> bool:
        seen.append(state)
        return state is sentinel

    gated_out = ControllerRegistration(controller=_CtlGatedOut, predicate=_never)
    gated_in = ControllerRegistration(controller=_CtlGatedIn, predicate=_predicate)
    _patch_discovery(
        monkeypatch,
        (FeatureManifest(name="a", controllers=(gated_out, gated_in)),),
    )
    api_handlers, root_handlers = collect_route_handlers(sentinel)
    assert api_handlers == [_CtlGatedIn]
    assert root_handlers == []
    assert seen == [sentinel]


def test_websocket_handlers_appended_to_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_discovery(
        monkeypatch,
        (
            FeatureManifest(
                name="a",
                controllers=(_CtlApi,),
                websocket_handlers=(_ws_handler,),
            ),
        ),
    )
    api_handlers, root_handlers = collect_route_handlers(_state())
    assert api_handlers == [_CtlApi, _ws_handler]
    assert root_handlers == []


def test_order_follows_discovery_and_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_reg = ControllerRegistration(controller=_CtlRoot, mount="root")
    gated_in = ControllerRegistration(controller=_CtlGatedIn, predicate=_always)
    _patch_discovery(
        monkeypatch,
        (
            FeatureManifest(name="a", controllers=(_CtlApi,)),
            FeatureManifest(name="b", controllers=(root_reg,)),
            FeatureManifest(
                name="c",
                controllers=(gated_in,),
                websocket_handlers=(_ws_handler,),
            ),
        ),
    )
    api_handlers, root_handlers = collect_route_handlers(_state())
    assert api_handlers == [_CtlApi, _CtlGatedIn, _ws_handler]
    assert root_handlers == [_CtlRoot]
