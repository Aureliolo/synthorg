# module-kind: orchestrator
"""Startup wiring for under-specified-team-work refinement.

Attaches a Chief-of-Staff-backed ``WorkRefinementRouter`` to the
already-built work pipeline. When team-bound work reaches the spine with
no definition of done, the router opens a clarify-and-propose
conversation instead of letting the coordinator's clarification gate
block it. Best-effort and idempotent: it runs after the Chief-of-Staff
proposer is wired (it wraps that proposer) and degrades to no router --
so the gate blocks under-specified team work -- when the proposer or the
work pipeline is absent (the Chief of Staff is off, or an empty-company
boot).
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_refinement_router(app_state: AppState) -> None:
    """Attach the Chief-of-Staff refinement router to the work pipeline.

    Best-effort and idempotent. A missing proposer (Chief of Staff off)
    or a missing work pipeline (empty-company boot) leaves the pipeline
    router-less: team-bound work with no definition of done is then
    blocked by the coordinator's clarification gate rather than refined.

    Raises:
        MemoryError: Propagated from router construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from router construction for the same
            reason.
        SubsystemDeclinedError: No proposer to refine with, or no work
            pipeline to attach the router to.
    """
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
    if proposer is None:
        msg = "no chief-of-staff proposer; refinement is its capability"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(EngineStateSlice).work_pipeline is None:
        msg = "no work pipeline; the router attaches to it"
        raise SubsystemDeclinedError(msg)
    try:
        from synthorg.meta.chief_of_staff.refinement import (  # noqa: PLC0415
            ChiefOfStaffRefinementRouter,
        )

        router = ChiefOfStaffRefinementRouter(proposer=proposer)
        work_pipeline_of(app_state).attach_refinement_router(router)
    except MemoryError, RecursionError:
        # Interpreter-level criticals are never best-effort; let them abort.
        raise
    except Exception as exc:
        # Reported, not returned: every declared dependency is present, so a
        # failure here is the build failing, and a quiet return leaves the
        # pipeline router-less for a reason only a container log carries.
        msg = f"refinement router wiring failed: {safe_error_description(exc)}"
        logger.warning(
            API_APP_STARTUP,
            service="work_refinement_router",
            note="refinement router wiring failed; pipeline unchanged",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise SubsystemDeclinedError(msg) from exc
    logger.info(API_APP_STARTUP, service="work_refinement_router", note="wired")


async def unwire_refinement_router(app_state: AppState) -> None:
    """Detach the refinement router from the work pipeline.

    The router binds the proposer instance at construction, so it has to go
    down with one: left attached it would keep refining through a proposer
    the operator's model change has already replaced.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    pipeline = app_state.slice(EngineStateSlice).work_pipeline
    if pipeline is None:
        return
    pipeline.attach_refinement_router(None)
    logger.info(API_APP_STARTUP, service="work_refinement_router", note="unwired")
