"""Service-resolution helpers + arg constants for memory MCP handlers.

Resolves the injected :class:`MemoryService` (routing through the
``app_state`` facade per the persistence-boundary rule), with a
fine-tune-required variant and a deletion-only variant. Shared by the
fine-tune, checkpoint, and entry handler modules.
"""

from typing import TYPE_CHECKING

from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.memory.service import MemoryService
from synthorg.memory.state import MemoryStateSlice, memory_service_of
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState

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


def _service(app_state: AppState) -> MemoryService:
    """Return the injected :class:`MemoryService` facade.

    Handlers route through the wired :class:`MemoryService` exclusively
    (CLAUDE.md persistence-boundary rule): reaching into the raw
    ``PersistenceStateSlice.backend`` to assemble fine-tune repositories
    from a meta-layer handler crosses that boundary, so the only honoured
    paths are a service attached as a plain attribute (stripped-down test
    app-states) or one published on :class:`MemoryStateSlice`. When neither
    is present the handler returns a uniform ``not_supported`` envelope.

    Raises:
        MemoryBackendUnsupportedError: When no :class:`MemoryService` is
            wired (attribute-attached or sliced).

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
    raise MemoryBackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)


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
