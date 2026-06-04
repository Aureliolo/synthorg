"""Run-support helpers for :class:`AgentEngine`.

Stakes-aware identity routing applied before the budget block, and the
best-effort flight-recorder frame recording run after the loop. Both
sit off the per-turn hot path and are mixed into the engine.
"""

from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import FLIGHT_RECORDER_RECORD_FAILED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.loop_protocol import ExecutionResult

logger = get_logger(__name__)


class AgentEngineRunMixin:
    """Stakes routing and flight-frame recording for the engine run."""

    # Populated on the concrete ``AgentEngine`` in ``__init__``; typed
    # ``Any`` because the mixin only reads them. The concrete class
    # carries the authoritative types.
    _stakes_router: Any
    _flight_recorder_sink: Any
    _clock: Any

    async def _route_stakes(
        self,
        identity: AgentIdentity,
        task: Task,
    ) -> AgentIdentity:
        """Apply stakes-aware routing, returning the adjusted identity.

        Delegates to the injected :class:`StakesRouter` to pick a model
        tier matched to ``task.stakes``. The red-team requirement carried
        on the decision is consumed downstream by the review pipeline,
        which derives it from the persisted ``task.stakes``; this method
        only adjusts the model the subtask runs with.

        Returns:
            ``identity`` with its model replaced when the router picks
            a different one; the original ``identity`` is returned
            unchanged when the router's selection matches.
        """
        assert self._stakes_router is not None  # noqa: S101  # caller checks
        decision = await self._stakes_router.route(task=task, identity=identity)
        if decision.selected_model == identity.model:
            return identity
        return identity.model_copy(update={"model": decision.selected_model})

    async def _record_flight_frames(
        self,
        execution_result: ExecutionResult,
        *,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Record flight-recorder frames for a finished run (best-effort).

        Runs after the loop has completed, so it is off the per-turn hot
        path. Both frame construction and recording are guarded here so
        a fault in ``build_frames`` (e.g. malformed conversation history,
        Pydantic validation regression) cannot turn a successful run
        into a failed one any more than a sink fault can. System errors
        still escape so the operator sees them; storage / construction
        faults log and return.
        """
        if self._flight_recorder_sink is None:
            return
        from synthorg.engine.flight_recording import build_frames  # noqa: PLC0415

        try:
            frames = build_frames(
                execution_result,
                execution_id=execution_result.context.execution_id,
                agent_id=agent_id,
                task_id=task_id,
                clock=self._clock,
            )
            if frames:
                await self._flight_recorder_sink.record_frames(frames)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                FLIGHT_RECORDER_RECORD_FAILED,
                execution_id=execution_result.context.execution_id,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
