# module-kind: adapter
"""Publishing a decomposition's progress onto the plan it is writing.

A recursive decomposition persists its tree once, at the end, so the plan reads
``PLANNING`` with zero items for the whole run. A live run sat there for 54
minutes while the page promised "items appear as they are written", and the
only way to tell a working decomposition from a hung one was the backend log.

This is the one implementation of
:class:`~synthorg.engine.decomposition.progress_protocol.DecompositionProgressReporter`
that reaches a durable row. It lives here rather than in the engine because it
needs the plan service, and the decomposition service deliberately holds no
repository: it decomposes tasks, and which row carries the answer is a wiring
question.
"""

from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.plan import DecompositionProgress
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of


class PlanRowProgressReporter:
    """Stamps each snapshot onto the ``PLANNING`` plan for that objective.

    The backend is resolved per report rather than held, because the
    coordinator this is wired into is built during construction and
    persistence connects on startup: a service captured at wiring time would
    be captured as absent, permanently. The same reason
    ``agent_state_repository_provider`` resolves late.
    """

    __slots__ = ("_app_state",)

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    async def report(
        self, *, objective_task_id: str, progress: DecompositionProgress
    ) -> None:
        """Write *progress* to the plan being decomposed for *objective_task_id*.

        Nothing else is caught here. The service calling this already treats
        reporting as best-effort and logs what it drops, so catching again
        would put a second, quieter policy on the same failure. An unconnected
        backend is the one exception, because it is not a failure: it is a
        deployment where nothing durable can carry the answer yet.

        Args:
            objective_task_id: The objective the tree is being planned for.
            progress: How far the tree has got.
        """
        try:
            backend = persistence_of(self._app_state)
        except ServiceUnavailableError:
            return
        plans = build_plan_service(backend, clock=self._app_state.clock)
        await plans.record_progress(
            parent_task_id=NotBlankStr(objective_task_id),
            progress=progress,
        )
