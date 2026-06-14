"""Persistence feature state slice.

Holds the connected persistence backend. ``None`` in dev /
empty-company runs with no backend configured; persistence-dependent
controllers raise 503 on a ``None`` backend. The idempotency service
(an API-layer wrapper over ``backend.idempotency_keys``) lives on the
api-core slice to keep this package free of an api-layer dependency.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionRecordRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.red_team_report_protocol import (
    RedTeamReportArchiveRepository,
)


class PersistenceStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the persistence feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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


def code_execution_records_of(
    app_state: AppStateSliceMixin,
) -> CodeExecutionRecordRepository | None:
    """Return the code-execution record repository, or ``None`` if unwired.

    Companion to :func:`persistence_of` for the optional capture path:
    the code-runner persists ``purpose='tests'`` runs into this
    append-only store, but a dev / empty-company run with no backend
    must still build its tool registry. Returning ``None`` lets the
    code-runner no-op its capture rather than 503-ing the whole runtime.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The append-only code-execution record repository, or ``None``
        when no backend is wired.
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    return backend.code_execution_records if backend is not None else None


def red_team_reports_of(
    app_state: AppStateSliceMixin,
) -> RedTeamReportArchiveRepository | None:
    """Return the durable red-team report archive, or ``None`` if unwired.

    Companion to :func:`persistence_of` for the optional audit-trail
    path: the red-team gate persists each evaluation's merged report +
    verdict into this append-only archive, but a dev / empty-company run
    with no backend must still build its red-team runtime. Returning
    ``None`` lets the gate skip archival (its write is fail-OPEN) rather
    than 503-ing the whole runtime.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The append-only red-team report archive, or ``None`` when no
        backend is wired.
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    return backend.red_team_reports if backend is not None else None


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
