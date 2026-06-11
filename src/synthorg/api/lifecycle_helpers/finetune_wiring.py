# module-kind: orchestrator
"""On-startup wiring for the embedding fine-tune orchestrator.

Split from :mod:`synthorg.api.lifecycle_helpers.feature_wiring` to keep
that module under its size budget. The construction stays under ``api/``
so the anti-ghost-wiring gate sees the ``FineTuneOrchestrator`` reachable
from the boot path.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

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

    from synthorg.memory.embedding.fine_tune_orchestrator import (  # noqa: PLC0415
        FineTuneOrchestrator,
    )
    from synthorg.memory.embedding.training_sources import (  # noqa: PLC0415
        TrajectoryTrainingDataSource,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415
    from synthorg.observability.events.memory import (  # noqa: PLC0415
        MEMORY_FINE_TUNE_WIRING_FAILED,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
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
        training_data_source = None
        memory_backend = memory_slice.backend
        if memory_backend is not None:
            history_dir = await config_resolver_of(app_state).get_str(
                SettingNamespace.META, "scorecard_history_dir"
            )
            training_data_source = TrajectoryTrainingDataSource(
                memory_backend=memory_backend,
                task_repo=backend.tasks,
                artifact_repo=backend.artifacts,
                scorecard_history_dir=Path(history_dir) if history_dir else None,
            )
        orchestrator = FineTuneOrchestrator(
            run_repo=run_repo,
            checkpoint_repo=checkpoint_repo,
            settings_service=app_state.slice(SettingsStateSlice).settings_service,
            training_data_source=training_data_source,
            clock=app_state.clock,
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
