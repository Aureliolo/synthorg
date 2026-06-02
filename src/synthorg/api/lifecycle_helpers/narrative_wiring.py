# module-kind: orchestrator
"""Startup wiring for documentary mode (the post-run run narrator).

Kept out of :mod:`feature_wiring` so that module stays under its
size-budget tier. The narrator reads the docs engine and project brain
(both wired earlier in ``wire_features_on_startup``) and attaches to the
already-built work pipeline; it is best-effort and idempotent.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.tracker import CostTracker
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def wire_run_narrator(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTracker | None,
) -> None:
    """Attach the post-run narrator to the pipeline behind narrative_enabled.

    Best-effort and idempotent: it runs after the docs engine and project
    brain are wired (the narrator reads both) and attaches to the
    already-built work pipeline. A disabled flag, an absent provider, or a
    missing collaborator leaves the pipeline narrator-less so completed
    briefs simply produce no narrative.
    """
    from synthorg.docs_engine.state import DocsStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.meta.chief_of_staff.narrative.factory import (  # noqa: PLC0415
        build_chief_of_staff_narrator,
    )
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if (
        provider_registry is None
        or app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).work_pipeline is None
    ):
        return
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    config = meta_self_improvement.chief_of_staff
    if not config.narrative_enabled:
        return
    available = provider_registry.list_providers()
    provider = provider_registry.get(available[0]) if available else None
    narrator = build_chief_of_staff_narrator(
        config,
        provider=provider,
        docs_service=app_state.slice(DocsStateSlice).service,
        brain_service=app_state.slice(ProjectBrainStateSlice).service,
        frames=persistence_of(app_state).flight_recorder_frames,
        task_repo=persistence_of(app_state).tasks,
        cost_tracker=cost_tracker,
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
