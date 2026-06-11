"""Service-resolution helpers + arg constants for memory MCP handlers.

Resolves the injected :class:`MemoryService` (routing through the
``app_state`` facade per the persistence-boundary rule), with a
fine-tune-required variant and a deletion-only variant. Shared by the
fine-tune, checkpoint, and entry handler modules.
"""

from synthorg.api.state import AppState
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.memory.service import MemoryService
from synthorg.memory.state import MemoryStateSlice, memory_service_of
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import SettingsStateSlice

_TY_NON_BLANK = "non-blank string"
_ARG_CHECKPOINT_ID = "checkpoint_id"
_ARG_RUN_ID = "run_id"
_ARG_AGENT_ID = "agent_id"
_ARG_MEMORY_ID = "memory_id"

_WHY_MEMORY_SERVICE_NOT_WIRED = (
    "memory service is not wired on the active application state; "
    "fine-tune endpoints require an injected MemoryService and are "
    "unavailable on backends that do not support fine-tune repositories"
)

_WHY_BACKEND_NO_FINE_TUNE = (
    "fine-tune repositories are not exposed by the active persistence "
    "backend; ensure the backend is connected and exposes "
    "fine_tune_runs + fine_tune_checkpoints (both SQLite and Postgres "
    "do today)"
)


def _service(app_state: AppState) -> MemoryService:
    """Return the injected :class:`MemoryService` facade.

    Handlers route through ``app_state.memory_service`` exclusively
    (CLAUDE.md persistence-boundary rule). For app_states that have
    adopted the wired-service pattern, :attr:`has_memory_service`
    short-circuits the lookup. As a fallback for stripped-down test
    app-states that expose only a raw ``persistence`` backend, we try
    to construct a service on the fly from
    ``persistence.fine_tune_checkpoints`` / ``.fine_tune_runs``.

    Every failure mode raises :class:`MemoryBackendUnsupportedError` so the
    calling handler returns a uniform ``not_supported`` envelope:

    * No wired service **and** the raw backend is absent / doesn't expose
      fine-tune repos.
    * The backend's fine-tune property raises ``NotImplementedError``
      (legacy / partial backend).
    * The backend is not yet connected and the property's
      ``_require_connected`` guard raises
      :class:`~synthorg.core.persistence_errors.PersistenceConnectionError`.

    Raises:
        MemoryBackendUnsupportedError: In any of the above cases.

    Returns:
        ``MemoryService`` instance.
    """
    # Probe the raw instance dict first so a stripped-down test
    # app-state -- e.g. a ``SimpleNamespace`` that sets ``memory_service``
    # as a plain attribute and has no ``slice`` method -- is served
    # before we ever touch ``app_state.slice``. Reading ``vars`` also
    # avoids triggering ``AppState.memory_service`` (a property descriptor
    # that raises ``RuntimeError`` when the slot has not been set).
    raw_cached = (
        vars(app_state).get("memory_service")
        if hasattr(app_state, "__dict__")
        else None
    )
    if isinstance(raw_cached, MemoryService):
        return raw_cached
    slice_fn = getattr(app_state, "slice", None)
    if slice_fn is not None and slice_fn(MemoryStateSlice).service is not None:
        attached: MemoryService = memory_service_of(app_state)
        return attached
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        raise MemoryBackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)
    try:
        checkpoint_repo = backend.fine_tune_checkpoints
        run_repo = backend.fine_tune_runs
    except (
        NotImplementedError,
        PersistenceConnectionError,
        AttributeError,
    ) as exc:
        # ``AttributeError`` covers partial backends that simply lack
        # the property altogether; without catching it here the handler
        # would surface a generic 500 instead of the contract-stipulated
        # ``not_supported`` envelope.
        raise MemoryBackendUnsupportedError(_WHY_BACKEND_NO_FINE_TUNE) from exc
    settings_service = app_state.slice(SettingsStateSlice).settings_service
    return MemoryService(
        checkpoint_repo=checkpoint_repo,
        run_repo=run_repo,
        settings_service=settings_service,
        memory_backend=app_state.slice(MemoryStateSlice).backend,
    )


def _delete_entry_service(app_state: AppState) -> MemoryService:
    """Return a :class:`MemoryService` suitable for memory-entry deletion.

    Sibling of :func:`_service` that does **not** require fine-tune
    repositories. The ``delete_memory_entry`` path only needs a
    :class:`MemoryBackend`; treating missing fine-tune repos as fatal
    here would route every memory-only deployment through
    ``not_supported`` and silently disable user data deletion.

    Resolution order:

    1. The wired :class:`MemoryService` facade
       (``app_state.has_memory_service``).
    2. A cached ``MemoryService`` attached as a plain attribute
       (stripped-down test app-states).
    3. A freshly-built ``MemoryService`` constructed from a wired
       ``MemoryBackend`` (with optional ``settings_service``); fine-tune
       repos are intentionally left as ``None``.

    Raises:
        MemoryBackendUnsupportedError: When no service or backend is wired at
            all -- the only case where deletion truly cannot proceed.

    Returns:
        ``MemoryService`` instance.
    """
    raw_cached = (
        vars(app_state).get("memory_service")
        if hasattr(app_state, "__dict__")
        else None
    )
    if isinstance(raw_cached, MemoryService):
        return raw_cached
    slice_fn = getattr(app_state, "slice", None)
    if slice_fn is not None and slice_fn(MemoryStateSlice).service is not None:
        attached: MemoryService = memory_service_of(app_state)
        return attached
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        raise MemoryBackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)
    settings_service = app_state.slice(SettingsStateSlice).settings_service
    return MemoryService(
        memory_backend=backend,
        settings_service=settings_service,
    )
