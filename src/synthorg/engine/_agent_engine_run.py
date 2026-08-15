# module-kind: service
"""Run-support helpers for :class:`AgentEngine`.

The phases a run passes through either side of the loop: settling what it is
bound to (which provider serves it, which identity it carries, how it samples),
rebuilding the context a prior execution left behind, and recording
flight-recorder frames once the loop is done. All sit off the per-turn hot path
and are mixed into the engine.
"""

from typing import TYPE_CHECKING, Final, NamedTuple

from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_budget_defaults import DEFAULT_MAX_TURNS
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import FLIGHT_RECORDER_RECORD_FAILED
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR
from synthorg.observability.events.session import SESSION_REPLAY_LOW_COMPLETENESS
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_UNDER_CAPABILITY,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.core.clock import Clock
    from synthorg.engine.flight_recording import FlightRecorderSink
    from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
    from synthorg.engine.session import EventReader
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_REPLAY_LOW_COMPLETENESS_THRESHOLD: Final[float] = 0.5
"""Log a warning when session replay completeness is below this."""


class RunBinding(NamedTuple):
    """What a run is committed to once the binding stages have spoken.

    The identity and its provider travel together because cost attribution
    reads ``identity.model.provider`` and the dispatched client must be that
    same connection. Neither is ever rewritten here: an agent is a fixed
    ``(role, personality, model)`` unit, so the binding stages settle how the
    run SAMPLES and nothing else.

    Attributes:
        provider: The client the run dispatches through.
        identity: The agent identity, exactly as it was handed in.
        completion_config: The run's sampling, or ``None`` for the defaults.
    """

    provider: CompletionProvider
    identity: AgentIdentity
    completion_config: CompletionConfig | None


class AgentEngineRunMixin:
    """Binding, session replay and flight-frame recording for a run."""

    # Populated on the concrete ``AgentEngine`` in ``__init__``; declared
    # here so the type checker sees them when the mixin reads them. The
    # concrete class owns the assignment.
    _capability: CapabilityPolicy | None
    _budget_enforcer: BudgetEnforcer | None
    _provider_registry: ProviderRegistry | None
    _flight_recorder_sink: FlightRecorderSink | None
    _event_reader: EventReader | None
    _clock: Clock
    _config_resolver: ConfigResolver | None

    async def _replay_session(
        self,
        *,
        resume_execution_id: str,
        identity: AgentIdentity,
        task: Task,
        max_turns: int,
    ) -> AgentContext | None:
        """Rebuild the context a prior execution left in the event stream.

        Replay is best-effort by construction: the events are a projection,
        so a partial one still resumes work that would otherwise be lost. A
        low completeness score is therefore logged rather than refused, and
        the caller folds whatever came back onto the fresh context.

        Returns:
            The replayed context, or ``None`` when no event reader is wired
            (nothing to replay from).
        """
        if self._event_reader is None:
            return None
        from synthorg.engine.session import Session  # noqa: PLC0415

        replayed = await Session.replay(
            execution_id=resume_execution_id,
            event_reader=self._event_reader,
            identity=identity,
            task=task,
            max_turns=max_turns,
        )
        if replayed.replay_completeness < _REPLAY_LOW_COMPLETENESS_THRESHOLD:
            logger.warning(
                SESSION_REPLAY_LOW_COMPLETENESS,
                execution_id=resume_execution_id,
                replay_completeness=replayed.replay_completeness,
            )
        return replayed.context

    @staticmethod
    def _merge_replayed(ctx: AgentContext, replayed: AgentContext) -> AgentContext:
        """Fold a replayed execution's accumulated state onto a fresh context.

        The replayed run's identity wins (execution id, start, cost, turn
        count) so the resumed run continues the original rather than opening
        a second one, and its conversation is appended AFTER the fresh
        context's, which carries the rebuilt system prompt. The task
        execution falls back to the fresh one, because a replay that never
        saw a transition has none to contribute.

        Returns:
            The context the loop resumes from.
        """
        return ctx.model_copy(
            update={
                "execution_id": replayed.execution_id,
                "started_at": replayed.started_at,
                "conversation": (*ctx.conversation, *replayed.conversation),
                "accumulated_cost": replayed.accumulated_cost,
                "turn_count": replayed.turn_count,
                "task_execution": replayed.task_execution or ctx.task_execution,
            },
        )

    async def _bind_run(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        provider: CompletionProvider,
        completion_config: CompletionConfig | None,
    ) -> RunBinding:
        """Settle what the run dispatches through, as, and how.

        Three stages, none of which rewrites the pair an operator bound to
        this agent. The capability check refuses outright when the agent runs
        below what its task demands and the stakes forbid the concession; the
        budget pre-flight refuses when a limit is already spent; and the
        reasoning depth and prompt caching fold into how the run samples.

        The capability check is the SAME
        :class:`~synthorg.engine.routing_policy.capability_policy.CapabilityPolicy`
        instance selection walked, so the two cannot reach different verdicts
        about this pair. It is a last line rather than a duplicate rule: a
        task can arrive assigned by hand, or reassigned after a failure,
        without ever passing selection.

        A stage that raises leaves the run bound to what this was handed: a
        binding commits as a set, so a failure part-way through attributes the
        run to the last binding that fully completed rather than to a half-
        applied one.

        Returns:
            The :class:`RunBinding` the run executes under.

        Raises:
            StakesModelUnavailableError: When the bound agent does not clear
                the capability its task demands and its stakes refuse a
                weaker one.
        """
        if self._capability is not None:
            # Folded here so higher-stakes work thinks harder on the model the
            # agent already is: the one stakes dial left on the call, because
            # it tunes how the bound model works rather than which model runs.
            completion_config = self._fold_stakes_reasoning(
                completion_config,
                identity,
                self._check_capability(identity, task),
            )

        if self._budget_enforcer:
            await self._budget_enforcer.check_can_execute(
                str(identity.id), provider_name=identity.model.provider
            )

        return RunBinding(
            provider=provider,
            identity=identity,
            completion_config=await self._fold_prompt_caching(
                completion_config, identity
            ),
        )

    def _dispatch_client_for(
        self,
        identity: AgentIdentity,
        fallback_provider: CompletionProvider,
    ) -> CompletionProvider:
        """Return the client that serves ``identity.model.provider``.

        The engine holds a single default client, but each agent can be
        pinned to any registered provider. Cost attribution and the budget
        preflight both read ``identity.model.provider``, so the dispatched
        client must be that same provider or a call hits one provider's API
        while the cost/quota lands on another. Resolving strictly against the
        registry keeps the client and the identity in lockstep; a miss (an
        agent pinned to an unregistered provider) raises
        ``DriverNotRegisteredError`` so the run fails cleanly here instead of
        silently dispatching a mismatched pair to the engine default. Falls
        back to ``fallback_provider`` only when no registry is wired at all
        (a degraded / test context with no catalogue to resolve against).

        Returns:
            The registry client for ``identity.model.provider``, or
            ``fallback_provider`` when no provider registry is wired.

        Raises:
            DriverNotRegisteredError: When a registry is wired but does not
                know ``identity.model.provider``.
        """
        if self._provider_registry is None:
            return fallback_provider
        return self._provider_registry.get(identity.model.provider)

    def _check_capability(
        self,
        identity: AgentIdentity,
        task: Task,
    ) -> ReasoningEffort | None:
        """Refuse an unsanctioned pair, and return the reasoning depth to use.

        Asks the same policy instance selection walked, so a task assigned by
        selection always clears here. What this catches is a pair selection
        never saw: a hand-assigned task, or one reassigned after a failure.

        A sanctioned-but-weaker agent is logged rather than refused, because
        below the park floor the org has already decided that a weaker agent
        doing the work beats the work not happening; every deliverable still
        passes the completion gates either way. The review pipeline gates the
        red team on the task's persisted stakes independently, so nothing is
        threaded from here.

        Returns:
            The stakes-driven reasoning effort (``None`` when the provider
            default should stand).

        Raises:
            StakesModelUnavailableError: When the bound agent does not clear
                the capability the task demands and its stakes refuse a
                weaker one.
        """
        assert self._capability is not None  # noqa: S101  # caller checks
        verdict = self._capability.judge(
            model=identity.model,
            stakes=task.stakes,
            complexity=task.estimated_complexity,
        )
        if not verdict.sanctioned:
            # No event here: an escalation is a park that persisted, and this
            # refusal reaches FAILED just as often. The handler owns the
            # emission so one refusal is one record.
            raise StakesModelUnavailableError(
                stakes=task.stakes,
                required_capability=verdict.required,
                unresolved=verdict.unresolved,
            )
        if verdict.fit == "lower":
            logger.warning(
                TASK_ASSIGNMENT_UNDER_CAPABILITY,
                task_id=str(task.id),
                agent_id=str(identity.id),
                path="dispatch",
                stakes=task.stakes.value,
                required_capability=verdict.required,
                agent_capability=verdict.agent,
                note=(
                    "Running below the rung this work demands; the stakes"
                    " sanction the concession."
                ),
            )
        return self._capability.reasoning_effort(task.stakes)

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

    async def _resolve_live_bool(
        self, namespace: str, key: str, *, fallback: bool = True
    ) -> bool:
        """Resolve a boolean setting live, fail-safe to ``fallback`` unwired.

        Shared by the per-run streaming / caching gates so both read the same
        DB > env > default chain without duplicating the outage fallback.

        Returns:
            The resolved flag, or ``fallback`` when no resolver is wired.
        """
        if self._config_resolver is None:
            return fallback
        from synthorg.settings.kill_switch import (  # noqa: PLC0415
            resolve_bool_with_fallback,
        )

        return await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=namespace,
            key=key,
            fallback=fallback,
        )

    async def _resolve_streaming_enabled(
        self,
        provider: CompletionProvider,
        identity: AgentIdentity,
        *,
        task_id: str,
    ) -> bool:
        """Decide whether the run streams its per-turn LLM calls.

        Streams only when the operator setting
        ``engine.work_loop_streaming_enabled`` (live per run, fail-safe to the
        default) is on AND the run's model advertises streaming support. A
        capability-lookup fault fails safe to the non-streaming path so a
        transient provider hiccup never blocks the run.

        Returns:
            ``True`` when the run should stream its per-turn calls.
        """
        from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

        enabled = await self._resolve_live_bool(
            SettingNamespace.ENGINE, "work_loop_streaming_enabled"
        )
        if not enabled:
            return False
        try:
            capabilities = await provider.get_model_capabilities(
                identity.model.model_id
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- degrade-to-non-streaming wiring
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=str(identity.id),
                task_id=task_id,
                note="streaming capability lookup failed; using non-streaming",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return capabilities.supports_streaming

    async def _fold_prompt_caching(
        self,
        completion_config: CompletionConfig | None,
        identity: AgentIdentity,
    ) -> CompletionConfig | None:
        """Turn on prompt caching for the run when the operator setting allows.

        Reads ``providers.prompt_caching_enabled`` live per run (fail-safe to
        the registered default on a settings outage). When enabled, sets the
        caching flag on the run config, building a fresh config that preserves
        the agent's temperature / max_tokens when the caller passed none. When
        disabled, leaves the config untouched. The driver still gates the
        actual ``cache_control`` placement on per-model caching support, so a
        non-caching model is unaffected either way.

        Returns:
            The completion config to run with, or ``None`` when unchanged.
        """
        from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

        enabled = await self._resolve_live_bool(
            SettingNamespace.PROVIDERS, "prompt_caching_enabled"
        )
        if not enabled:
            return completion_config
        base = completion_config or CompletionConfig(
            temperature=identity.model.temperature,
            max_tokens=identity.model.max_tokens,
        )
        return base.model_copy(update={"prompt_caching": True})

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
