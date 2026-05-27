"""Per-feature state-slice store for ``AppState``.

The slice store is the substrate replacement for ``AppState``'s
god-attribute-bag: each feature composes one frozen
:class:`~synthorg._core.features.BaseFeatureStateSlice` at boot, and
controllers read their feature's slice via :meth:`AppStateSliceMixin.slice`
rather than reaching for a bare service attribute.

Kept in its own mixin module (not ``state.py``) so the slice mechanism does
not inflate the ``api/state.py`` god-module while the per-feature migration
is in flight; ``state.py`` only adds the mixin to ``AppState``'s bases.
"""

import threading
from typing import Any, cast

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


class AppStateSliceMixin:
    """Typed per-feature state-slice store mixed into ``AppState``.

    Slices are keyed by their concrete class. ``set_slice`` is once-only
    (mirroring the historic ``set_<service>`` seams); ``swap_slice`` hot-
    replaces an already-composed slice atomically under the slice lock, so a
    reader holding the old slice keeps its references and the next ``slice``
    call returns the new one.
    """

    def _init_slice_store(self) -> None:
        """Initialise the slice store. Called once from ``AppState.__init__``."""
        self._slices: dict[type[BaseFeatureStateSlice], BaseFeatureStateSlice] = {}
        self._slice_lock: threading.Lock = threading.Lock()

    def has_slice(self, slice_type: type[BaseFeatureStateSlice]) -> bool:
        """Return whether a slice of the given type has been composed.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.

        Returns:
            ``True`` when the slice is present (lets boot-time composition stay
            idempotent across a lifespan re-entry).
        """
        return slice_type in self._slices

    def slice[SliceT: BaseFeatureStateSlice](self, slice_type: type[SliceT]) -> SliceT:
        """Return the state slice of the given type, composing it if absent.

        Boot composes every feature's slice up front, so this normally
        returns the already-composed instance. When a slice has not been
        composed yet (a bare ``AppState`` in tests, or a wiring seam that
        runs before ``compose_feature_slices``), an empty slice (all
        service fields ``None``) is composed lazily under the lock so a
        reader never faces an absent slice; the controller still raises
        503 on a ``None`` field.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.

        Returns:
            The composed slice instance.
        """
        composed = self._slices.get(slice_type)
        if composed is None:
            with self._slice_lock:
                composed = self._slices.get(slice_type)
                if composed is None:
                    composed = slice_type()
                    self._slices[slice_type] = composed
        return cast("SliceT", composed)

    def set_slice(self, state_slice: BaseFeatureStateSlice) -> None:
        """Compose a feature state slice once at boot.

        Args:
            state_slice: The slice to install, keyed by its concrete type.

        Raises:
            RuntimeError: If a slice of the same type is already composed.
                Hot-reload replacement goes through :meth:`swap_slice`.
        """
        with self._slice_lock:
            key = type(state_slice)
            if key in self._slices:
                logger.error(
                    API_APP_STARTUP,
                    action="slice_already_configured",
                    slice=key.__name__,
                )
                msg = f"State slice {key.__name__} already configured"
                raise RuntimeError(msg)
            self._slices[key] = state_slice

    def swap_slice(self, state_slice: BaseFeatureStateSlice) -> None:
        """Hot-replace a feature state slice.

        The replacement is atomic under the slice lock: a reader that already
        holds the old slice keeps its references, and the next ``slice`` call
        returns the new one (the same whole-old-or-new guarantee the legacy
        per-service swap seams provided).

        Args:
            state_slice: The replacement slice, keyed by its concrete type.
        """
        with self._slice_lock:
            self._slices[type(state_slice)] = state_slice

    def wire[SliceT: BaseFeatureStateSlice](
        self,
        slice_type: type[SliceT],
        /,
        **updates: Any,
    ) -> None:
        """Wire service references into a feature slice (field-level swap).

        Reads the current slice (composing an empty one if absent),
        produces a frozen copy with *updates* applied, and atomically
        installs it under the slice lock. The one-line replacement for
        the historic per-service ``set_<service>`` seams:
        ``app_state.wire(XStateSlice, field=service)``.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.
            **updates: Slice field values to set on the replacement copy.
        """
        with self._slice_lock:
            current = self._slices.get(slice_type) or slice_type()
            self._slices[slice_type] = current.model_copy(update=updates)
