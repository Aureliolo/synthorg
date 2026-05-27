"""Boot-time composition of per-feature state slices.

Composes an empty (all-``None``) state slice for every discovered feature so
controllers can read their slice immediately; the feature wiring hooks in
``api.app`` swap in the populated slice once the backing services are built.

Kept out of ``api/app.py`` so the boot step does not inflate that god-module.
"""

from typing import TYPE_CHECKING

from synthorg._core.features import discover_features

if TYPE_CHECKING:
    from synthorg.api.state import AppState


def compose_feature_slices(app_state: AppState) -> None:
    """Compose an empty slice for every discovered feature with one.

    Idempotent across a lifespan re-entry (shared-app test fixtures): a slice
    already composed by a prior cycle is left in place for the wiring hooks to
    swap, so this never wipes populated slices.

    Args:
        app_state: The application state to compose slices onto.
    """
    for feature in discover_features():
        slice_type = feature.state_slice
        if slice_type is not None and not app_state.has_slice(slice_type):
            app_state.set_slice(slice_type())
