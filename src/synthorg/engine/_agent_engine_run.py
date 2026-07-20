"""Run-support helpers for :class:`AgentEngine`.

Stakes-aware identity routing applied before the budget block, and the
best-effort flight-recorder frame recording run after the loop. Both
sit off the per-turn hot path and are mixed into the engine.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import DEFAULT_MAX_TURNS
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import FLIGHT_RECORDER_RECORD_FAILED
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR
from synthorg.providers.models import CompletionConfig

if TYPE_CHECKING:
    from synthorg.core.clock import Clock
    from synthorg.engine.flight_recording import FlightRecorderSink
    from synthorg.engine.routing_policy.router import StakesRouter
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class AgentEngineRunMixin:
    """Stakes routing and flight-frame recording for the engine run."""

    # Populated on the concrete ``AgentEngine`` in ``__init__``; declared
    # here so the type checker sees them when the mixin reads them. The
    # concrete class owns the assignment.
    _stakes_router: StakesRouter | None
    _flight_recorder_sink: FlightRecorderSink | None
    _clock: Clock
    _config_resolver: ConfigResolver | None

    async def _route_stakes(
        self,
        identity: AgentIdentity,
        task: Task,
    ) -> tuple[AgentIdentity, ReasoningEffort | None]:
        """Apply stakes-aware routing, returning the adjusted identity.

        Delegates to the injected :class:`StakesRouter` to pick a model
        tier matched to ``task.stakes``; this method adjusts the model the
        subtask runs with and surfaces the stakes-driven reasoning effort so
        the caller can fold it into the run's completion config. The review
        pipeline independently gates the red-team review on the task's
        persisted ``task.stakes`` (see ``run_completion_gates`` /
        ``red_team_min_stakes``), so the routing decision's
        ``red_team_required`` flag is not threaded from here.

        Returns:
            A ``(identity, reasoning_effort)`` pair: ``identity`` with its
            model replaced when the router picks a different one (else the
            original), and the stakes-driven reasoning effort (``None`` when
            the provider default should stand).
        """
        assert self._stakes_router is not None  # noqa: S101  # caller checks
        decision = await self._stakes_router.route(task=task, identity=identity)
        reasoning_effort = decision.reasoning_effort
        if decision.selected_model == identity.model:
            return identity, reasoning_effort
        routed = identity.model_copy(update={"model": decision.selected_model})
        return routed, reasoning_effort

    @staticmethod
    def _fold_stakes_reasoning(
        completion_config: CompletionConfig | None,
        identity: AgentIdentity,
        reasoning_effort: ReasoningEffort | None,
    ) -> CompletionConfig | None:
        """Fold the stakes-driven reasoning effort into the run config.

        Leaves the config untouched (possibly ``None``, so the loop builds
        its own default) when no reasoning effort is requested. Otherwise
        builds on the caller-supplied config, or a fresh one carrying the
        agent's temperature / max_tokens, so those are preserved alongside
        the reasoning dial.

        Returns:
            The completion config to run with, or ``None`` when unchanged.
        """
        if reasoning_effort is None:
            return completion_config
        base = completion_config or CompletionConfig(
            temperature=identity.model.temperature,
            max_tokens=identity.model.max_tokens,
        )
        return base.model_copy(update={"reasoning_effort": reasoning_effort})

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
                summary_max_chars=self._flight_recorder_sink.summary_max_chars,
                clock=self._clock,
            )
            if frames:
                await self._flight_recorder_sink.record_frames(frames)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            logger.warning(
                FLIGHT_RECORDER_RECORD_FAILED,
                execution_id=execution_result.context.execution_id,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _resolve_max_turns(self, *, agent_id: str, task_id: str) -> int:
        """Resolve the per-run turn cap from settings, falling back to default.

        Returns:
            The operator-configured ``engine.max_turns`` when a resolver is
            wired and the value is positive, else :data:`DEFAULT_MAX_TURNS`.
            A settings-backend outage fails safe to the default.
        """
        if self._config_resolver is None:
            return DEFAULT_MAX_TURNS
        try:
            resolved = await self._config_resolver.get_int("engine", "max_turns")
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- degrade-to-None wiring
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                note="failed to read engine.max_turns, using default",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return DEFAULT_MAX_TURNS
        return resolved if resolved > 0 else DEFAULT_MAX_TURNS
