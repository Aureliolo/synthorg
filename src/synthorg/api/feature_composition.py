"""Boot-time composition of per-feature state slices.

Composes an empty (all-``None``) state slice for every discovered feature so
controllers can read their slice immediately; the feature wiring hooks in
``api.app`` swap in the populated slice once the backing services are built.

Kept out of ``api/app.py`` so the boot step does not inflate that god-module.
"""

from litestar import Controller
from litestar.handlers import WebsocketRouteHandler

from synthorg._core.features import ControllerRegistration, discover_features
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

type RouteHandlerEntry = type[Controller] | WebsocketRouteHandler


def compose_feature_slices(app_state: AppState) -> None:
    """Compose an empty slice for every discovered feature with one.

    Idempotent across a lifespan re-entry (shared-app test fixtures): a slice
    already composed by a prior cycle is left in place for the wiring hooks to
    swap, so this never wipes populated slices.

    Args:
        app_state: The application state to compose slices onto.
    """
    composed = 0
    for feature in discover_features():
        slice_type = feature.state_slice
        if slice_type is not None and not app_state.has_slice(slice_type):
            app_state.set_slice(slice_type())
            composed += 1
    if composed:
        logger.info(
            API_APP_STARTUP,
            action="feature_slices_composed",
            composed=composed,
        )


def collect_route_handlers(
    app_state: AppState,
) -> tuple[list[RouteHandlerEntry], list[RouteHandlerEntry]]:
    """Collect REST + websocket route handlers from every discovered feature.

    Replaces the hand-maintained ``BASE_CONTROLLERS`` /
    ``INTEGRATION_CONTROLLERS`` / ``OPTIONAL_CONTROLLERS`` lists: the
    composition root iterates the feature manifests instead. Each manifest
    entry is a bare ``type[Controller]`` or a
    :class:`~synthorg._core.features.ControllerRegistration`; a registration
    may carry a predicate (mount only when it returns ``True`` against the
    live ``AppState``, preserving the historic 404-when-unwired behaviour for
    integration / optional controllers) and a mount point (``"api"`` under the
    API prefix, ``"root"`` at the application root).

    Predicates are evaluated here, at route-assembly time, so a controller
    gated on a service wired only later in startup stays unmounted exactly as
    it does today.

    Args:
        app_state: The constructed application state passed to predicates.

    Returns:
        ``(api_handlers, root_handlers)``: handlers to mount under the API
        prefix (controllers + websocket handlers) and at the application
        root, in deterministic feature-dependency order.
    """
    api_handlers: list[RouteHandlerEntry] = []
    root_handlers: list[RouteHandlerEntry] = []
    for feature in discover_features():
        for entry in feature.controllers:
            registration = (
                entry
                if isinstance(entry, ControllerRegistration)
                else ControllerRegistration(controller=entry)
            )
            if registration.predicate is not None and not registration.predicate(
                app_state
            ):
                continue
            target = root_handlers if registration.mount == "root" else api_handlers
            target.append(registration.controller)
        api_handlers.extend(feature.websocket_handlers)
    return api_handlers, root_handlers
