"""Persistence feature state slice.

Holds the connected persistence backend. ``None`` in dev /
empty-company runs with no backend configured; persistence-dependent
controllers raise 503 on a ``None`` backend. The idempotency service
(an API-layer wrapper over ``backend.idempotency_keys``) lives on the
api-core slice to keep this package free of an api-layer dependency.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.persistence.protocol import PersistenceBackend

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class PersistenceStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the persistence feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: PersistenceBackend | None = None


def persistence_of(app_state: AppStateSliceMixin) -> PersistenceBackend:
    """Return the connected persistence backend, or raise 503.

    The backend lives on the persistence state slice. Persistence-bound
    controllers read it through this accessor so the slice lookup is
    centralised here rather than repeated at every call site; a
    dev / empty-company run with no backend surfaces a clean
    ``ServiceUnavailableError``.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The connected persistence backend.

    Raises:
        ServiceUnavailableError: When no backend is configured.
    """
    return require_service(
        app_state.slice(PersistenceStateSlice).backend, "Persistence"
    )


def optional_persistence_of(
    app_state: AppStateSliceMixin,
) -> PersistenceBackend | None:
    """Return the connected persistence backend, or ``None`` when absent.

    Companion to :func:`persistence_of` for call sites that can operate
    without a backend and must not raise when a dev / empty-company run
    has no backend wired. Used to wire persistence-bound repositories
    that are themselves optional (e.g. the code-runner's append-only
    record store), so a backend-less runtime still builds rather than
    503-ing the whole tool registry.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The connected persistence backend, or ``None`` when unwired.
    """
    return app_state.slice(PersistenceStateSlice).backend


def persistence_backend_label(app_state: AppStateSliceMixin) -> str:
    """Return the persistence backend class name, or ``"unwired"`` if absent.

    Diagnostic-only helper for log paths that must run even when the
    backend slot is unwired (e.g. orchestrator-unavailable branches
    that still want to record which backend was active). Unlike
    :func:`persistence_of`, this never raises.

    Returns:
        Backend class name, or ``"unwired"`` when the backend is ``None``.
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    return type(backend).__name__ if backend is not None else "unwired"
