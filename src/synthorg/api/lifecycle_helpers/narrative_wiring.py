# module-kind: orchestrator
"""Startup wiring for documentary mode (the post-run run narrator).

Kept out of :mod:`feature_wiring` so that module stays under its
size-budget tier. The narrator reads the docs engine and project brain, so
the ``run_narrator`` subsystem names both in its ``requires`` and the
reconciler orders it after them; it attaches to the already-built work
pipeline and is best-effort and idempotent.
"""

from synthorg.api.state import AppState
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def wire_run_narrator(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Attach the post-run narrator to the pipeline; gating is live per run.

    The narrator is attached unconditionally of ``narrative_enabled``; the
    capability is gated live per run inside the narrator, so the flag flips
    on without a restart. Best-effort and idempotent: it runs after the docs
    engine and project brain are wired (the narrator reads both) and attaches
    to the already-built work pipeline. An absent provider or a missing
    collaborator leaves the pipeline narrator-less so completed briefs simply
    produce no narrative.

    Raises:
        MemoryError: Propagated from narrator construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from narrator construction for the same
            reason.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if (
        provider_registry is None
        or app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).work_pipeline is None
    ):
        return
    config = si_config.chief_of_staff
    # The narrator is attached unconditionally of ``narrative_enabled``: it
    # gates documentary mode live per run, so the flag flips on without a
    # restart. Construction is best-effort: a misconfigured collaborator
    # must not abort app startup, so a failure leaves the pipeline
    # narrator-less (completed briefs simply produce no narrative) rather
    # than propagating. Mirrors the charter / research wiring helpers.
    try:
        _attach_narrator(
            app_state,
            config=config,
            provider_registry=provider_registry,
            cost_tracker=cost_tracker,
            master_enabled=si_config.chief_of_staff_enabled,
        )
    except MemoryError, RecursionError:
        # Interpreter-level criticals are never best-effort; let them abort.
        raise
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log and continue
        logger.warning(
            API_APP_STARTUP,
            service="chief_of_staff_narrator",
            note="narrator construction failed; pipeline unchanged",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _attach_narrator(
    app_state: AppState,
    *,
    config: ChiefOfStaffConfig,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None,
    master_enabled: bool,
) -> None:
    """Build the narrator and attach it to the work pipeline.

    Pulls the run-cost currency from the wired budget config (falling back
    to the system default) so the narrative renders costs with a unit.
    """
    from synthorg.api._feature_provider_resolution import (  # noqa: PLC0415
        resolve_feature_provider,
    )
    from synthorg.budget.currency import DEFAULT_CURRENCY  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.docs_engine.state import DocsStateSlice  # noqa: PLC0415
    from synthorg.engine.state import work_pipeline_of  # noqa: PLC0415
    from synthorg.meta.chief_of_staff.narrative.factory import (  # noqa: PLC0415
        build_chief_of_staff_narrator,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    provider = resolve_feature_provider(
        provider_registry,
        config.narrative_model,
        feature="chief_of_staff_narrator",
    )
    budget_config = app_state.slice(BudgetStateSlice).budget_config
    currency = budget_config.currency if budget_config else DEFAULT_CURRENCY
    narrator = build_chief_of_staff_narrator(
        config,
        provider=provider,
        docs_service=app_state.slice(DocsStateSlice).service,
        brain_service=app_state.slice(ProjectBrainStateSlice).service,
        frames=persistence_of(app_state).flight_recorder_frames,
        task_repo=persistence_of(app_state).tasks,
        cost_tracker=cost_tracker,
        currency=currency,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        master_enabled=master_enabled,
    )
    if narrator is None:
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_narrator",
            note="narrative enabled but collaborators absent; pipeline unchanged",
        )
        return
    work_pipeline_of(app_state).attach_narrator(narrator)
    logger.info(API_APP_STARTUP, service="chief_of_staff_narrator", note="wired")


async def unwire_run_narrator(app_state: AppState) -> None:
    """Detach the narrator so the next pass rebuilds it.

    It captured the docs service and the project brain at construction, so
    once either is replaced the narrator is reading a run through services
    nothing else uses. Detaching is also what the reconciler reads as the
    subsystem being down, since liveness comes from the pipeline's own
    attachment record.

    Args:
        app_state: Application state that owns the work pipeline.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    pipeline = app_state.slice(EngineStateSlice).work_pipeline
    if pipeline is None:
        return
    pipeline.attach_narrator(None)
    logger.info(API_APP_STARTUP, service="chief_of_staff_narrator", note="unwired")
