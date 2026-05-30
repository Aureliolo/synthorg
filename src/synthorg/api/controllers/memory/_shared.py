"""Shared memory-service construction for the memory admin controllers."""

from synthorg.api.state import AppState
from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.memory.service import MemoryService
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
    FineTuneRunRepository,
)
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


def build_memory_service(
    app_state: AppState,
    *,
    require_fine_tune: bool = True,
) -> MemoryService:
    """Construct a :class:`MemoryService` from the current AppState.

    Kept on the controller package rather than :class:`AppState` so the
    service layer depends on AppState (and not vice-versa) and the
    AppState slot inventory stays stable. Resolves the fine-tune
    repositories through :class:`PersistenceBackend` so the controller
    does not hard-wire the SQLite implementation.

    The ``require_fine_tune`` flag separates the fine-tune-admin
    endpoints (which need both checkpoint + run repos and translate a
    missing backend implementation into HTTP 501) from memory-only
    endpoints such as the ``DELETE /memory/entries/...`` path,
    which only need the ``MemoryBackend``. Without this carve-out a
    Postgres deployment that wires a memory backend without fine-tune
    support would 501 on every entry deletion even though
    :class:`MemoryService.delete_memory_entry` can run without the
    fine-tune repos.

    Args:
        app_state: Active application state.
        require_fine_tune: When ``True`` (default), eagerly resolve
            ``fine_tune_checkpoints`` / ``fine_tune_runs`` and raise
            :class:`FeatureNotImplementedError` (HTTP 501) when they
            are absent.  When ``False``, leave the repos as ``None``
            so the service constructs cleanly for memory-only
            endpoints.

    Raises:
        FeatureNotImplementedError: When ``require_fine_tune`` is
            ``True`` and the backend does not implement the fine-tune
            repositories (HTTP 501).

    Returns:
        ``MemoryService`` instance.
    """
    backend = persistence_of(app_state)
    checkpoint_repo: FineTuneCheckpointRepository | None = None
    run_repo: FineTuneRunRepository | None = None
    if require_fine_tune:
        try:
            checkpoint_repo = backend.fine_tune_checkpoints
            run_repo = backend.fine_tune_runs
        except NotImplementedError as exc:
            msg = (
                "Fine-tune admin endpoints are not supported by the "
                "active persistence backend."
            )
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                backend=type(backend).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise FeatureNotImplementedError(msg) from exc
    return MemoryService(
        checkpoint_repo=checkpoint_repo,
        run_repo=run_repo,
        settings_service=(app_state.slice(SettingsStateSlice).settings_service),
        memory_backend=app_state.slice(MemoryStateSlice).backend,
    )
