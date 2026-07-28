# module-kind: code
"""Boot wiring for the durable agent-memory backend.

This is the seam the whole memory layer hangs from: the project brain,
the knowledge substrate, living docs and agent memory itself all read
whatever ``MemoryStateSlice.backend`` holds, so a backend that is
ephemeral or absent here degrades every one of them at once.

Two rules shape this module:

* It runs **before** ``_install_runtime_services``. ``_construct_agent_engine``
  reads ``MemoryStateSlice.backend`` eagerly, so a backend wired after
  that point would never reach an agent.
* It **fails loud**. When no embedding model resolves, no backend is
  wired and the failure is logged at ERROR. Falling back to keyword-only
  memory would look like working memory while quietly recalling the
  wrong things, which is exactly the failure mode this replaces. An
  operator who genuinely wants that trade-off selects it explicitly via
  ``memory.backend``.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.memory.config import EmbedderOverrideConfig
from synthorg.memory.consolidation.cycle_scheduler import AgentIdSupplier
from synthorg.memory.embedder_port import TextEmbedder
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    SCHEDULER_DISABLED,
    SCHEDULER_START_FAILED,
)
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_WIRE_FAILED,
    MEMORY_BACKEND_WIRE_SKIPPED,
    MEMORY_BACKEND_WIRED,
    MEMORY_EMBEDDER_RESOLVED,
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
    MEMORY_EMBEDDER_UNRESOLVED,
)

logger = get_logger(__name__)


async def wire_memory_backend(app_state: AppState) -> None:
    """Build and wire the durable memory backend.

    Idempotent, and gated on connected persistence because the durable
    backend stores vectors in the same database as everything else.
    """
    from synthorg.memory.factory import (  # noqa: PLC0415
        IN_MEMORY_BACKEND,
        MemoryBackendDeps,
        create_memory_backend,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(MemoryStateSlice).backend is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.warning(MEMORY_BACKEND_WIRE_SKIPPED, reason="persistence_not_connected")
        return

    memory_config = app_state.config.memory
    embedder = None
    if memory_config.backend != IN_MEMORY_BACKEND:
        embedder = await _build_embedder(app_state)
        if embedder is None:
            logger.warning(MEMORY_BACKEND_WIRE_SKIPPED, reason="no_embedder_resolved")
            return

    try:
        backend = create_memory_backend(
            memory_config,
            deps=MemoryBackendDeps(
                repository=persistence_of(app_state).memory_vectors,
                embedder=embedder,
                clock=app_state.clock,
            ),
        )
        await backend.connect()
    except Exception as exc:  # noqa: BLE001 -- reported, then startup continues
        reraise_critical(exc)
        logger.error(
            MEMORY_BACKEND_WIRE_FAILED,
            backend=memory_config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return

    # Recorded on the slice because the health surface has to name which
    # embedder is serving, and a connected backend no longer carries that.
    app_state.wire(
        MemoryStateSlice,
        backend=backend,
        embedder_ref=embedder.model_ref if embedder is not None else None,
    )
    logger.info(
        MEMORY_BACKEND_WIRED,
        backend=memory_config.backend,
        durable=memory_config.backend != IN_MEMORY_BACKEND,
        dense_search=backend.supports_dense_search,
    )
    await _wire_consolidation_scheduler(app_state, backend)


async def _wire_consolidation_scheduler(
    app_state: AppState,
    backend: MemoryBackend,
) -> None:
    """Start the periodic consolidation and retention driver.

    Without this the consolidation subsystem is inert: three strategies,
    a batch-size setting and a kill switch that nothing ever calls, so
    memory grows unbounded and archival never runs while the settings
    page advertises otherwise.

    Best-effort: a scheduler that fails to start is reported and the rest
    of memory stays up, because losing maintenance is worse than losing
    recall but neither is worth failing boot over.
    """
    from synthorg.memory.consolidation.cycle_scheduler import (  # noqa: PLC0415
        MemoryConsolidationScheduler,
        interval_seconds_for,
    )
    from synthorg.memory.consolidation.service import (  # noqa: PLC0415
        MemoryConsolidationService,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    # Optional on both the service and the scheduler: without it the
    # kill switch reads its registered default rather than failing boot.
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    consolidation = app_state.config.memory.consolidation
    interval = interval_seconds_for(consolidation.interval)
    if interval is None:
        logger.info(SCHEDULER_DISABLED, interval=consolidation.interval.value)
        return

    scheduler = MemoryConsolidationScheduler(
        MemoryConsolidationService(
            backend=backend,
            config=consolidation,
            config_resolver=resolver,
        ),
        interval_seconds=interval,
        agent_ids=_agent_id_supplier(app_state),
        config_resolver=resolver,
    )
    try:
        await scheduler.start()
    except Exception as exc:  # noqa: BLE001 -- reported, then startup continues
        reraise_critical(exc)
        logger.error(
            SCHEDULER_START_FAILED,
            interval_seconds=interval,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    # The scheduler base class emits its own started event, so this
    # records only the wiring outcome the health surface reads.
    app_state.wire(MemoryStateSlice, consolidation_scheduler=scheduler)


def _agent_id_supplier(app_state: AppState) -> AgentIdSupplier:
    """Build the roster source the scheduler iterates each tick.

    Read fresh per tick rather than captured at boot, so agents hired
    after startup are maintained too.

    Returns:
        A supplier of the current agent identifiers.
    """
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    async def _supply() -> tuple[NotBlankStr, ...]:
        states = await persistence_of(app_state).agent_states.list_items()
        return tuple(NotBlankStr(str(state.agent_id)) for state in states)

    return _supply


async def _build_embedder(app_state: AppState) -> TextEmbedder | None:
    """Resolve the embedder, or report loudly why memory stays off.

    Returns:
        The embedder, or ``None`` when no embedding model resolves, in
        which case the caller must not wire a backend.
    """
    from synthorg.memory.embedding.probe import is_builtin_embedder  # noqa: PLC0415
    from synthorg.memory.embedding.resolve import (  # noqa: PLC0415
        resolve_embedder_config,
    )

    # The operator's choice is persisted to ``memory.embedder_*`` during
    # setup, so at boot those settings are the whole answer. Nothing is
    # selected here.
    try:
        config = await resolve_embedder_config(
            app_state.config.memory,
            settings_override=await _settings_override(app_state),
        )
    except Exception as exc:  # noqa: BLE001 -- reported, then startup continues
        reraise_critical(exc)
        logger.error(
            MEMORY_EMBEDDER_UNRESOLVED,
            remedy=(
                "choose an embedding model in setup, or set "
                "memory.embedder_model to a provider-bound reference"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Memory stays off, loudly. Starting the built-in embedder here
        # would turn a configuration the operator needs to fix into a
        # working-looking system with materially weaker recall.
        return None
    logger.info(
        MEMORY_EMBEDDER_RESOLVED,
        provider=config.provider,
        model=config.model,
        dims=config.dims,
    )
    if is_builtin_embedder(config.provider, config.model):
        from synthorg.memory.embedding.hashing import (  # noqa: PLC0415
            HashingTextEmbedder,
        )

        return HashingTextEmbedder(dims=config.dims)

    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.memory.embedding.text_embedder import (  # noqa: PLC0415
        ProviderTextEmbedder,
    )

    return ProviderTextEmbedder(
        config,
        cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
    )


async def _settings_override(app_state: AppState) -> EmbedderOverrideConfig | None:
    """Read the operator's embedder override from runtime settings.

    Returns:
        The override, or ``None`` when unset or unreadable, in which case
        resolution reads the YAML config alone and refuses if that names
        no model either.
    """
    from synthorg.settings.model_ref import parse_model_ref  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    settings = app_state.slice(SettingsStateSlice).settings_service
    if settings is None:
        return None
    try:
        raw_model = (await settings.get("memory", "embedder_model")).value
        dims = (await settings.get("memory", "embedder_dims")).value
    except Exception as exc:  # noqa: BLE001 -- reported, then the YAML config decides
        reraise_critical(exc)
        logger.warning(
            MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    ref = parse_model_ref(raw_model) if raw_model else None
    if ref is None and not dims:
        return None
    # A ref that parsed to a model with no provider stays provider-less
    # here on purpose: resolution refuses it by name rather than this
    # layer quietly inventing the missing half.
    return EmbedderOverrideConfig(
        provider=(ref.provider or None) if ref is not None else None,
        model=(ref.model_id or None) if ref is not None else None,
        dims=int(dims) if dims else None,
    )
