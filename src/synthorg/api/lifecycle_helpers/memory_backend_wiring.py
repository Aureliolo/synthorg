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

import contextlib

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.memory.config import CompanyMemoryConfig, EmbedderOverrideConfig
from synthorg.memory.consolidation.cycle_scheduler import AgentIdSupplier
from synthorg.memory.embedder_port import TextEmbedder
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    SCHEDULER_DISABLED,
    SCHEDULER_START_FAILED,
)
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_SETTINGS_READ_FAILED,
    MEMORY_BACKEND_UNWIRED,
    MEMORY_BACKEND_WIRE_FAILED,
    MEMORY_BACKEND_WIRE_SKIPPED,
    MEMORY_BACKEND_WIRED,
    MEMORY_EMBEDDER_RESOLVED,
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
    MEMORY_EMBEDDER_UNRESOLVED,
)
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint
from synthorg.providers.state import embedding_endpoint_resolver_of

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

    memory_config = await _resolved_memory_config(app_state)
    embedder = None
    if memory_config.backend != IN_MEMORY_BACKEND:
        embedder = await _build_embedder(app_state)
        if embedder is None:
            logger.warning(MEMORY_BACKEND_WIRE_SKIPPED, reason="no_embedder_resolved")
            return
    # Cleared the moment the embedder is known good, not once the whole pass
    # succeeds: anything that fails after this point is not the embedder, and
    # leaving a previous pass's reason on the slice would send an operator to
    # fix the half they already fixed.
    app_state.wire(MemoryStateSlice, wiring_failure=None)

    backend = create_memory_backend(
        memory_config,
        deps=MemoryBackendDeps(
            repository=persistence_of(app_state).memory_vectors,
            embedder=embedder,
            clock=app_state.clock,
        ),
    )
    try:
        await backend.connect()
    except Exception as exc:  # noqa: BLE001 -- reported, then startup continues
        reraise_critical(exc)
        logger.error(
            MEMORY_BACKEND_WIRE_FAILED,
            backend=memory_config.backend,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Named on the slice for the same reason an embedder failure is: the
        # health surface would otherwise fall back to advice about choosing an
        # embedding model, which is the half that just worked.
        app_state.wire(
            MemoryStateSlice,
            wiring_failure=(
                f"The {memory_config.backend} store refused the connection: "
                f"{safe_error_description(exc)}"
            ),
        )
        return
    except BaseException:
        # A shutdown delivered inside ``connect()`` leaves a half-open
        # connection reachable only from this frame: the slice never
        # takes it, and shutdown disconnects only what the slice holds.
        # Best-effort because a failed cleanup must not replace the
        # cancellation on its way out.
        with contextlib.suppress(Exception):
            await backend.disconnect()
        raise

    # Recorded on the slice because the health surface has to name which
    # embedder is serving, and a connected backend no longer carries that.
    app_state.wire(
        MemoryStateSlice,
        backend=backend,
        embedder_ref=embedder.model_ref if embedder is not None else None,
        wiring_failure=None,
    )
    logger.info(
        MEMORY_BACKEND_WIRED,
        backend=memory_config.backend,
        durable=memory_config.backend != IN_MEMORY_BACKEND,
        dense_search=backend.supports_dense_search,
    )
    await _wire_consolidation_scheduler(app_state, backend, memory_config)


async def _resolved_memory_config(app_state: AppState) -> CompanyMemoryConfig:
    """Return the memory config with the operator's current choices applied.

    The boot config mirrors ``memory.backend`` from the environment only, so
    reading it alone would ignore a value written through the dashboard: the
    rebuild the reconciler correctly triggered would then construct the same
    backend it just tore down.

    Returns:
        The boot config, with backend and consolidation interval replaced by
        the resolved values when the resolver can supply them.
    """
    from synthorg.memory.enums import ConsolidationInterval  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    config = app_state.config.memory
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        return config
    try:
        backend = await resolver.get_str("memory", "backend")
        interval = ConsolidationInterval(
            await resolver.get_str("memory", "consolidation_interval")
        )
        # Re-validated rather than copied in: ``model_copy(update=...)`` skips
        # validation, so a stored backend naming nothing would travel all the
        # way to ``create_memory_backend`` before anything objected. A value
        # that fails here is the same problem as one that could not be read,
        # and takes the same path back to the boot config.
        resolved = type(config).model_validate(
            config.model_dump()
            | {
                "backend": backend,
                "consolidation": config.consolidation.model_dump()
                | {"interval": interval},
            }
        )
    except Exception as exc:  # noqa: BLE001 -- reported, then the boot config decides
        reraise_critical(exc)
        logger.warning(
            MEMORY_BACKEND_SETTINGS_READ_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return config
    return resolved


async def unwire_memory_backend(app_state: AppState) -> None:
    """Take the memory backend down so the next pass can rebuild it.

    Runs when an operator changes the embedder, the backend kind or the
    consolidation interval: every one of those is baked in at connect time, so
    the running instance has to go before a new one can take its place.

    Teardown is best-effort per step. A backend that refuses to disconnect
    must not leave the slice pointing at it, because the next pass would then
    read memory as up and never rebuild. Each failure is reported, though: a
    scheduler that would not stop is a task still running against a backend
    nothing points at, and only the log says so.

    Args:
        app_state: Application state holding the memory slice.
    """
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415

    slice_ = app_state.slice(MemoryStateSlice)
    scheduler = slice_.consolidation_scheduler
    backend = slice_.backend
    app_state.wire(
        MemoryStateSlice,
        backend=None,
        embedder_ref=None,
        consolidation_scheduler=None,
    )
    if scheduler is not None:
        try:
            await scheduler.stop()
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown step
            reraise_critical(exc)
            logger.warning(
                MEMORY_BACKEND_UNWIRED,
                step="consolidation_scheduler_stop",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    if backend is not None:
        try:
            await backend.disconnect()
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown step
            reraise_critical(exc)
            logger.warning(
                MEMORY_BACKEND_UNWIRED,
                step="backend_disconnect",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    logger.info(MEMORY_BACKEND_UNWIRED)


async def _wire_consolidation_scheduler(
    app_state: AppState,
    backend: MemoryBackend,
    memory_config: CompanyMemoryConfig,
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
    consolidation = memory_config.consolidation
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
    except BaseException:
        # ``start()`` arms a periodic loop before this frame records the
        # scheduler on the slice. A shutdown delivered in between would
        # leave that loop running with nothing able to reach it again,
        # for the life of the process.
        with contextlib.suppress(Exception):
            await scheduler.stop()
        raise
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
    # The operator's choice is persisted to ``memory.embedder_*`` during
    # setup, so at boot those settings are the whole answer. Nothing is
    # selected here.
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.memory.embedding.probe import is_builtin_embedder  # noqa: PLC0415
    from synthorg.memory.embedding.resolve import (  # noqa: PLC0415
        resolve_embedder_config,
    )

    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    # Resolved endpoints are kept so the serving embedder below reuses the one
    # the probe already looked up. Every resolution decrypts a credential, and
    # doing it a second time outside the guard below would put the one failure
    # this function exists to report on a path that cannot report it.
    resolved: dict[str, EmbeddingEndpoint] = {}
    lookup = embedding_endpoint_resolver_of(app_state)

    async def resolve_endpoint(provider: str) -> EmbeddingEndpoint:
        """Resolve *provider*'s endpoint once per wiring pass.

        Returns:
            Where the provider is reachable, and how to authenticate.
        """
        if provider not in resolved:
            resolved[provider] = await lookup(provider)
        return resolved[provider]

    try:
        config = await resolve_embedder_config(
            app_state.config.memory,
            settings_override=await _settings_override(app_state),
            cost_tracker=cost_tracker,
            resolve_endpoint=resolve_endpoint,
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
        # The health surface reads this. Without it an operator who has
        # chosen a model is told to choose one, because an unwired slice
        # cannot say which half of the binding failed.
        from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415

        app_state.wire(
            MemoryStateSlice,
            wiring_failure=safe_error_description(exc),
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

    from synthorg.memory.embedding.text_embedder import (  # noqa: PLC0415
        ProviderTextEmbedder,
    )

    # A plain read, not a resolution: the probe above already looked this
    # provider up inside the guarded block, so nothing here can fail.
    return ProviderTextEmbedder(
        config,
        cost_tracker=cost_tracker,
        endpoint=resolved.get(config.provider),
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
