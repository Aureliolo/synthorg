# module-kind: orchestrator
"""On-startup wiring for the embedding fine-tune orchestrator.

The construction stays under ``api/`` so the anti-ghost-wiring gate
sees the ``FineTuneOrchestrator`` reachable from the boot path.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.model_ref import parse_model_ref

logger = get_logger(__name__)


async def _wire_fine_tune_orchestrator(app_state: AppState) -> None:
    """Wire the embedding fine-tune orchestrator once persistence exists.

    Best-effort and gated on a connected persistence backend that implements
    the fine-tune repositories (backends without support raise
    ``NotImplementedError`` from those accessors, leaving the controllers to
    501). The orchestrator owns the background five-stage pipeline; once wired
    it marks any run interrupted by a prior crash as ``FAILED``. When a memory
    backend is present it also gets a :class:`TrajectoryTrainingDataSource` so a
    ``data_source=trajectory`` run harvests the org's real working history
    rather than a static directory; without a memory backend trajectory mode is
    unavailable and directory mode still works. A failure degrades the
    fine-tune controllers to 501 rather than poisoning startup.
    """
    from pathlib import Path  # noqa: PLC0415

    from synthorg.budget.state import cost_tracker_of  # noqa: PLC0415
    from synthorg.memory.embedding.fine_tune_docker_runner import (  # noqa: PLC0415
        FineTuneContainerRunner,
    )
    from synthorg.memory.embedding.fine_tune_image_resolution import (  # noqa: PLC0415
        get_resolved_fine_tune_image,
    )
    from synthorg.memory.embedding.fine_tune_models import (  # noqa: PLC0415
        FineTuneExecutionConfig,
    )
    from synthorg.memory.embedding.fine_tune_orchestrator import (  # noqa: PLC0415
        FineTuneOrchestrator,
    )
    from synthorg.memory.embedding.fine_tune_query import (  # noqa: PLC0415
        build_query_generator,
    )
    from synthorg.memory.embedding.fine_tune_run_helpers import (  # noqa: PLC0415
        resolve_execution_config,
    )
    from synthorg.memory.embedding.fine_tune_stage_executor import (  # noqa: PLC0415
        DockerStageExecutor,
        InProcessStageExecutor,
        StageExecutor,
    )
    from synthorg.memory.embedding.training_sources import (  # noqa: PLC0415
        TrajectoryTrainingDataSource,
    )
    from synthorg.memory.errors import (  # noqa: PLC0415
        FineTuneStageExecutionError,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415
    from synthorg.observability.events.memory import (  # noqa: PLC0415
        MEMORY_FINE_TUNE_WIRING_FAILED,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.providers.state import (  # noqa: PLC0415
        has_active_provider,
        provider_registry_of,
    )
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        config_resolver_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    memory_slice = app_state.slice(MemoryStateSlice)
    if memory_slice.fine_tune_orchestrator is not None:
        return
    backend = persistence_of(app_state)
    try:
        run_repo = backend.fine_tune_runs
        checkpoint_repo = backend.fine_tune_checkpoints
    except NotImplementedError:
        logger.info(
            API_APP_STARTUP,
            service="fine_tune_orchestrator",
            note="backend lacks fine-tune support; wiring skipped",
        )
        return
    try:
        resolver = config_resolver_of(app_state)
        training_data_source = None
        memory_backend = memory_slice.backend
        if memory_backend is not None:
            history_dir = await resolver.get_str(
                SettingNamespace.META, "scorecard_history_dir"
            )
            max_tasks_per_status = await resolver.get_int(
                SettingNamespace.MEMORY, "fine_tune_max_tasks_per_status"
            )
            per_agent_memory_limit = await resolver.get_int(
                SettingNamespace.MEMORY, "fine_tune_per_agent_memory_limit"
            )
            training_data_source = TrajectoryTrainingDataSource(
                memory_backend=memory_backend,
                task_repo=backend.tasks,
                artifact_repo=backend.artifacts,
                scorecard_history_dir=Path(history_dir) if history_dir else None,
                max_tasks_per_status=max_tasks_per_status,
                per_agent_memory_limit=per_agent_memory_limit,
            )
        # Stage-1 query generation is LLM-backed only when an operator sets
        # ``fine_tune_query_model`` AND a provider is registered; otherwise
        # the orchestrator falls back to the extractive generator (no LLM
        # cost). Directory mode needs no memory backend, so resolve this
        # regardless of the trajectory source above.
        # ``fine_tune_query_model`` is a model-assignment setting storing a
        # ``ModelRef``, so the model id and its provider are read together.
        query_generator = None
        query_ref = parse_model_ref(
            await resolver.get_str(SettingNamespace.MEMORY, "fine_tune_query_model")
        )
        query_model = query_ref.model_id.strip()
        if query_model and has_active_provider(app_state):
            registry = provider_registry_of(app_state)
            provider_name = query_ref.provider.strip()
            provider = None
            if not provider_name:
                # A bound MODEL_REF always carries its provider (the write-time
                # validator rejects a provider-less ref), so a blank provider
                # here means the model id was set without one: fall back to the
                # explicit default system provider, never a first-registered
                # pick (``None`` when the default is ambiguous/unset).
                provider = registry.default_provider()
                if provider is None:
                    logger.warning(
                        API_APP_STARTUP,
                        service="fine_tune_orchestrator",
                        note=(
                            "fine_tune_query_model has no provider and "
                            "providers.default_provider is ambiguous/unset; "
                            "using the extractive query generator"
                        ),
                        fine_tune_query_model=query_model,
                    )
            elif provider_name in registry:
                provider = registry.get(provider_name)
            else:
                # The operator named a provider that is not registered.
                # Surface the misconfiguration rather than silently
                # substituting a different provider; fall back to the
                # extractive query generator (provider stays None). A clean
                # degrade, so WARNING (matching the sibling wiring helpers),
                # not ERROR.
                logger.warning(
                    API_APP_STARTUP,
                    service="fine_tune_orchestrator",
                    note=(
                        "fine_tune_query_model's provider is not registered; "
                        "using the extractive query generator"
                    ),
                    provider_name=provider_name,
                    fine_tune_query_model=query_model,
                )
            if provider is not None:
                query_generator = build_query_generator(
                    provider=provider,
                    model=query_model,
                    cost_tracker=cost_tracker_of(app_state),
                )
        elif query_model:
            # The operator explicitly configured an LLM query model but no
            # provider is registered (or the registry is not yet wired).
            # Guard on ``has_active_provider`` so ``provider_registry_of``
            # cannot raise a 503 and abort orchestrator startup; surface the
            # intent-discard and fall back to the extractive query generator.
            logger.warning(
                API_APP_STARTUP,
                service="fine_tune_orchestrator",
                note=(
                    "fine_tune_query_model is set but no provider is "
                    "registered; using the extractive query generator"
                ),
                fine_tune_query_model=query_model,
            )
        # Execution backend wiring: the default resolves per run start
        # (docker when a fine-tune image is configured, else in-process),
        # and the factory routes docker-backed runs into ephemeral stage
        # containers. Both closures read hot settings through the
        # resolver at call time, so operator changes apply per run.
        container_runner = FineTuneContainerRunner(clock=app_state.clock)

        async def _resolve_default_execution() -> FineTuneExecutionConfig:
            # Runs at run-start time, long after the boot best-effort
            # block: a malformed setting here must surface as the typed
            # stage error the orchestrator's failure path expects, not
            # an anonymous raw exception.
            try:
                return resolve_execution_config(
                    None,
                    fine_tune_image=get_resolved_fine_tune_image(),
                    default_gpu=await resolver.get_bool(
                        SettingNamespace.MEMORY, "fine_tune_default_gpu"
                    ),
                    default_memory_limit=await resolver.get_str(
                        SettingNamespace.MEMORY, "fine_tune_memory_limit"
                    ),
                    default_timeout_seconds=await resolver.get_float(
                        SettingNamespace.MEMORY, "fine_tune_stage_timeout_seconds"
                    ),
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    MEMORY_FINE_TUNE_WIRING_FAILED,
                    service="fine_tune_orchestrator",
                    operation="resolve_default_execution",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = (
                    "could not resolve the default fine-tune execution"
                    f" config from settings: {safe_error_description(exc)}"
                )
                raise FineTuneStageExecutionError(msg) from exc

        async def _make_stage_executor(
            execution: FineTuneExecutionConfig | None,
        ) -> StageExecutor:
            try:
                if execution is not None and execution.backend == "docker":
                    data_volume = await resolver.get_str(
                        SettingNamespace.MEMORY, "fine_tune_data_volume"
                    )
                    return DockerStageExecutor(
                        execution=execution,
                        runner=container_runner,
                        data_volume=data_volume,
                    )
                return InProcessStageExecutor()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    MEMORY_FINE_TUNE_WIRING_FAILED,
                    service="fine_tune_orchestrator",
                    operation="make_stage_executor",
                    backend=execution.backend if execution is not None else None,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = (
                    "could not build the fine-tune stage executor:"
                    f" {safe_error_description(exc)}"
                )
                raise FineTuneStageExecutionError(msg) from exc

        orchestrator = FineTuneOrchestrator(
            run_repo=run_repo,
            checkpoint_repo=checkpoint_repo,
            settings_service=app_state.slice(SettingsStateSlice).settings_service,
            query_generator=query_generator,
            training_data_source=training_data_source,
            clock=app_state.clock,
            stage_executor_factory=_make_stage_executor,
            resolve_default_execution=_resolve_default_execution,
        )
        recovered = await orchestrator.recover_interrupted()
        app_state.wire(MemoryStateSlice, fine_tune_orchestrator=orchestrator)
        logger.info(
            API_APP_STARTUP,
            service="fine_tune_orchestrator",
            note="wired",
            recovered=recovered,
            trajectory_source=training_data_source is not None,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_FINE_TUNE_WIRING_FAILED,
            service="fine_tune_orchestrator",
            operation="startup_wire",
            note="fine-tune orchestrator wiring raised; controllers stay 501",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
