"""Per-feature state-slice store for ``AppState``.

Each feature composes one frozen
:class:`~synthorg._core.features.BaseFeatureStateSlice` at boot, keyed by
its concrete class; controllers read their feature's slice via
:meth:`AppStateSliceMixin.slice` (or a ``*_of`` accessor) for typed,
immutable access to that feature's services.

The store lives in its own mixin so the slice mechanism stays isolated
from ``api/state.py``; ``AppState`` mixes it in alongside the
cross-cutting mutable primitives a frozen slice cannot own.
"""

import threading
from typing import cast

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


class AppStateSliceMixin:
    """Typed per-feature state-slice store mixed into ``AppState``.

    Slices are keyed by their concrete class. ``set_slice`` is once-only
    (a second install of the same slice type raises); ``swap_slice`` hot-
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
        service fields ``None``) is composed lazily under the lock
        (double-checked, so concurrent first-readers share one instance)
        so a reader never faces an absent slice; the controller still
        raises 503 on a ``None`` field.

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
        returns the new one (a whole-old-or-new guarantee, never a partially
        updated slice).

        Args:
            state_slice: The replacement slice, keyed by its concrete type.
        """
        with self._slice_lock:
            self._slices[type(state_slice)] = state_slice

    def wire[SliceT: BaseFeatureStateSlice](
        self,
        slice_type: type[SliceT],
        /,
        **updates: object,
    ) -> None:
        """Wire service references into a feature slice (field-level swap).

        Reads the current slice (composing an empty one if absent),
        produces a frozen copy with *updates* applied, and atomically
        installs it under the slice lock. One call installs a service into
        its owning slice: ``app_state.wire(XStateSlice, field=service)``.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.
            **updates: Slice field values to set on the replacement copy.
        """
        with self._slice_lock:
            current = self._slices.get(slice_type) or slice_type()
            self._slices[slice_type] = current.model_copy(update=updates)

    def set_field_once(
        self,
        slice_type: type[BaseFeatureStateSlice],
        field: str,
        value: object,
        label: str,
    ) -> None:
        """Install a single slice field once, atomically.

        Holds the slice lock across the presence check and the write so a
        concurrent caller cannot pass the check before the field is set.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.
            field: The slice field to install.
            value: The service reference to set.
            label: Human-readable name for the "already configured" error.

        Raises:
            RuntimeError: If *field* already holds a non-``None`` value.
        """
        with self._slice_lock:
            current = self._slices.get(slice_type) or slice_type()
            if getattr(current, field) is not None:
                msg = f"{label} already configured"
                raise RuntimeError(msg)
            self._slices[slice_type] = current.model_copy(update={field: value})

    def wire_if_field_absent(
        self,
        slice_type: type[BaseFeatureStateSlice],
        field: str,
        value: object,
    ) -> bool:
        """Install a single slice field only if currently unset, atomically.

        The presence check and the write share one lock acquisition, so two
        concurrent callers cannot both install: the second observes the
        first's write and skips.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.
            field: The slice field to install when absent.
            value: The service reference to set.

        Returns:
            ``True`` if this call installed the field, ``False`` if a value
            was already present.
        """
        with self._slice_lock:
            current = self._slices.get(slice_type) or slice_type()
            if getattr(current, field) is not None:
                return False
            self._slices[slice_type] = current.model_copy(update={field: value})
            return True

    def swap_field_returning_previous(
        self,
        slice_type: type[BaseFeatureStateSlice],
        field: str,
        value: object,
    ) -> object:
        """Hot-replace a single slice field, returning the previous value.

        Reads the previous value and installs the replacement under one lock
        acquisition, so a concurrent swap cannot read a stale previous and
        drop a value the caller must close.

        Args:
            slice_type: The feature's :class:`BaseFeatureStateSlice` subclass.
            field: The slice field to replace.
            value: The replacement service reference.

        Returns:
            The value previously held in *field* (``None`` on first install).
        """
        with self._slice_lock:
            current = self._slices.get(slice_type) or slice_type()
            previous = getattr(current, field)
            self._slices[slice_type] = current.model_copy(update={field: value})
            return previous
