# module-kind: code
"""Boot wiring for the durable agent-memory backend.

This is the seam the whole memory layer hangs from. Before it existed,
``MemoryStateSlice.backend`` was populated as a side effect of the
training-service auto-wire with an ephemeral in-process store, so every
consumer (the project brain, the knowledge substrate, living docs and
agent memory itself) was silently doing substring matching over a dict
that emptied on restart, while the settings page advertised a durable
backend.

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
from synthorg.observability.events.api import API_APP_STARTUP

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
        logger.warning(
            API_APP_STARTUP,
            service="memory_backend",
            note="persistence not connected; agent memory unavailable",
        )
        return

    memory_config = app_state.config.memory
    embedder = None
    if memory_config.backend != IN_MEMORY_BACKEND:
        embedder = await _build_embedder(app_state)
        if embedder is None:
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
            API_APP_STARTUP,
            service="memory_backend",
            note="construction failed; agent memory unavailable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return

    app_state.wire(MemoryStateSlice, backend=backend)
    logger.info(
        API_APP_STARTUP,
        service="memory_backend",
        note="wired",
        backend=memory_config.backend,
        durable=memory_config.backend != IN_MEMORY_BACKEND,
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
        logger.info(
            API_APP_STARTUP,
            service="memory_consolidation",
            note="interval is 'never'; no scheduler started",
        )
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
            API_APP_STARTUP,
            service="memory_consolidation",
            note="scheduler failed to start; memory will not be maintained",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    app_state.wire(MemoryStateSlice, consolidation_scheduler=scheduler)
    logger.info(
        API_APP_STARTUP,
        service="memory_consolidation",
        note="scheduler started",
        interval_seconds=interval,
    )


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
    from synthorg.memory.embedding.resolve import (  # noqa: PLC0415
        resolve_embedder_config,
    )
    from synthorg.memory.embedding.text_embedder import (  # noqa: PLC0415
        ProviderTextEmbedder,
    )

    # Auto-selection from live provider models happens once, in the setup
    # wizard, which persists the winner to ``memory.embedder_*``. At boot
    # those settings are the source of truth, so an empty candidate list
    # here is expected rather than a degraded path.
    try:
        config = resolve_embedder_config(
            app_state.config.memory,
            settings_override=await _settings_override(app_state),
        )
    except Exception as exc:  # noqa: BLE001 -- reported, then startup continues
        reraise_critical(exc)
        logger.error(
            API_APP_STARTUP,
            service="memory_backend",
            note=(
                "no embedding model resolved; agent memory is OFF. "
                "Set memory.embedder_provider and memory.embedder_model, "
                "or connect a provider that offers an embedding model"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    logger.info(
        API_APP_STARTUP,
        service="memory_backend",
        note="embedder resolved",
        provider=config.provider,
        model=config.model,
        dims=config.dims,
    )
    return ProviderTextEmbedder(config)


async def _settings_override(app_state: AppState) -> EmbedderOverrideConfig | None:
    """Read the operator's embedder override from runtime settings.

    Returns:
        The override, or ``None`` when unset or unreadable, in which case
        resolution falls back to the YAML config and auto-selection.
    """
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    settings = app_state.slice(SettingsStateSlice).settings_service
    if settings is None:
        return None
    try:
        provider = (await settings.get("memory", "embedder_provider")).value
        model = (await settings.get("memory", "embedder_model")).value
        dims = (await settings.get("memory", "embedder_dims")).value
    except Exception as exc:  # noqa: BLE001 -- degrade to auto-selection
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="memory_backend",
            note="could not read embedder overrides; using auto-selection",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not provider and not model:
        return None
    return EmbedderOverrideConfig(
        provider=provider or None,
        model=model or None,
        dims=int(dims) if dims else None,
    )
