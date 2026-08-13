# module-kind: service
"""Run-support helpers for :class:`AgentEngine`.

The phases a run passes through either side of the loop: settling what it is
bound to (which provider serves it, which identity it carries, how it samples),
rebuilding the context a prior execution left behind, and recording
flight-recorder frames once the loop is done. All sit off the per-turn hot path
and are mixed into the engine.
"""

from typing import TYPE_CHECKING, Final, NamedTuple

from synthorg.budget.degradation import PreFlightResult
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import DegradationAction
from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_budget_defaults import DEFAULT_MAX_TURNS
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import FLIGHT_RECORDER_RECORD_FAILED
from synthorg.observability.events.degradation import DEGRADATION_PROVIDER_SWAPPED
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR
from synthorg.observability.events.session import SESSION_REPLAY_LOW_COMPLETENESS
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_BUDGET_OVERRODE,
    STAKES_ROUTING_PROVIDER_SWITCHED,
    STAKES_ROUTING_PROVIDER_UNRESOLVED,
)
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.core.clock import Clock
    from synthorg.engine.flight_recording import FlightRecorderSink
    from synthorg.engine.routing_policy.router import StakesRouter
    from synthorg.engine.session import EventReader
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_REPLAY_LOW_COMPLETENESS_THRESHOLD: Final[float] = 0.5
"""Log a warning when session replay completeness is below this."""


class RunBinding(NamedTuple):
    """What a run is committed to once routing and the budget have spoken.

    The three travel together because each stage can change any of them and
    they are only consistent as a set: routing may pick a model owned by a
    different provider, the budget may claw that tier back and re-point the
    provider again, and both fold into how the run samples.

    Attributes:
        provider: The client the run dispatches through.
        identity: The agent identity, with whatever model survived.
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
    _stakes_router: StakesRouter | None
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

        The three stages run in the one order that keeps them honest: stakes
        routing sets the target tier from the task, the budget then gets the
        last word (a hard ceiling must win over a stakes upgrade, so it may
        claw that tier back), and prompt caching folds into whatever sampling
        survived. Every provider swap commits together with the identity that
        justified it, so cost attribution (``identity.model.provider``) and the
        API actually called can never name different providers.

        A stage that raises leaves the run bound to what this was handed: a
        binding commits as a set, so a failure part-way through attributes the
        run to the last binding that fully completed rather than to a half-
        applied one.

        Returns:
            The :class:`RunBinding` the run executes under.
        """
        if self._stakes_router is not None:
            routed, reasoning_effort = await self._route_stakes(identity, task)
            provider, identity = self._resolve_provider_instance(
                routed,
                identity,
                provider,
            )
            # Folded here so higher-stakes work thinks harder, not only on a
            # stronger tier. temperature / max_tokens are stable across the
            # budget downgrade below, so the fold survives it.
            completion_config = self._fold_stakes_reasoning(
                completion_config, identity, reasoning_effort
            )

        if self._budget_enforcer:
            provider, identity = await self._apply_budget_ceiling(
                identity=identity,
                task=task,
                provider=provider,
            )

        # Last, on the final identity: the driver still gates the actual
        # cache_control placement on per-model caching support.
        return RunBinding(
            provider=provider,
            identity=identity,
            completion_config=await self._fold_prompt_caching(
                completion_config, identity
            ),
        )

    async def _apply_budget_ceiling(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Lower the run's binding to what the budget allows.

        Returns:
            ``(provider, identity)`` after the pre-flight degradation and any
            tier downgrade, each swap dispatched before it is committed.

        Raises:
            QuotaExhaustedError: When degradation selects a fallback provider
                the registry cannot serve.
            DriverNotRegisteredError: When the downgraded model names a
                provider the registry does not know.
        """
        assert self._budget_enforcer is not None  # noqa: S101  # caller checks
        agent_id = str(identity.id)
        preflight = await self._budget_enforcer.check_can_execute(
            agent_id, provider_name=identity.model.provider
        )
        provider, identity = self._apply_degradation(preflight, identity, provider)
        pre_downgrade_tier = identity.model.model_tier
        downgraded = await self._budget_enforcer.resolve_model(identity)
        if (
            self._stakes_router is not None
            and downgraded.model.model_tier != pre_downgrade_tier
        ):
            # Budget is a hard ceiling that wins over the stakes upgrade;
            # record when it clawed a stakes-driven tier back.
            logger.info(
                STAKES_ROUTING_BUDGET_OVERRODE,
                agent_id=agent_id,
                task_id=str(task.id),
                stakes_tier=pre_downgrade_tier,
                downgraded_to=downgraded.model.model_tier,
            )
        # resolve_model may downgrade to a model owned by another provider;
        # re-dispatch and only commit the new identity once dispatch succeeds,
        # so a registry miss never leaves a downgraded identity paired with the
        # pre-downgrade client for the fallback / recovery path to reuse.
        return self._dispatch_client_for(downgraded, provider), downgraded

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

    def _resolve_provider_instance(
        self,
        routed: AgentIdentity,
        fallback_identity: AgentIdentity,
        fallback_provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Return the client that serves the routed model's provider.

        Stakes routing can pick a model owned by a provider other than the
        engine default. Cost attribution reads ``identity.model.provider``,
        so the dispatched client must be that same provider or a call would
        hit one provider's API while the cost lands on another. Mirrors
        :meth:`_apply_degradation`'s registry lookup.

        When the routed provider matches the pre-routing one, the instance is
        unchanged (only the model id/tier moved). When it cannot be resolved
        (no registry wired, or a name the registry does not know), the
        pre-routing ``fallback_identity`` + ``fallback_provider`` are kept so
        instance and attribution stay in lockstep: a routing miss is never a
        mis-attribution.

        Returns:
            ``(provider, identity)``: the resolved client + routed identity,
            or the fallback pair when the routed provider is unresolvable.
        """
        target = routed.model.provider
        if target == fallback_identity.model.provider:
            # Same provider as before routing. ``fallback_provider`` was
            # resolved for that provider at run start (``_dispatch_client_for``),
            # so it already serves ``target``; only the model id/tier moved.
            return fallback_provider, routed
        if self._provider_registry is None:
            logger.warning(
                STAKES_ROUTING_PROVIDER_UNRESOLVED,
                routed_provider=target,
                reason="no_provider_registry",
                result="kept_default",
            )
            return fallback_provider, fallback_identity
        try:
            new_provider = self._provider_registry.get(target)
        except DriverNotRegisteredError as exc:
            logger.warning(
                STAKES_ROUTING_PROVIDER_UNRESOLVED,
                routed_provider=target,
                reason="not_in_registry",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                result="kept_default",
            )
            return fallback_provider, fallback_identity
        logger.info(
            STAKES_ROUTING_PROVIDER_SWITCHED,
            from_provider=fallback_identity.model.provider,
            to_provider=target,
            model_id=routed.model.model_id,
        )
        return new_provider, routed

    def _resolve_fallback_provider(
        self,
        effective: str,
        *,
        original: str,
    ) -> CompletionProvider:
        """Return the client for a degradation-selected fallback provider.

        Both failure branches raise rather than keeping the original client:
        degradation selected the fallback because the original is out of
        quota, so continuing on it would spend past the ceiling that triggered
        the swap.

        Returns:
            The registry client serving *effective*.

        Raises:
            QuotaExhaustedError: When no ``provider_registry`` is wired, or
                the registry does not know *effective*.
        """
        if self._provider_registry is None:
            logger.warning(
                DEGRADATION_PROVIDER_SWAPPED,
                original_provider=original,
                fallback_provider=effective,
                error="no provider_registry available",
                result="failed",
            )
            msg = (
                f"FALLBACK selected provider {effective!r} "
                f"but no provider_registry available"
            )
            raise QuotaExhaustedError(
                msg,
                provider_name=original,
                degradation_action=DegradationAction.FALLBACK,
            )
        try:
            return self._provider_registry.get(effective)
        except DriverNotRegisteredError as exc:
            logger.warning(
                DEGRADATION_PROVIDER_SWAPPED,
                original_provider=original,
                fallback_provider=effective,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                result="failed",
            )
            msg = f"Fallback provider {effective!r} not found in registry"
            raise QuotaExhaustedError(
                msg,
                provider_name=original,
                degradation_action=DegradationAction.FALLBACK,
            ) from exc

    def _apply_degradation(
        self,
        preflight: PreFlightResult,
        identity: AgentIdentity,
        provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Apply degradation result: swap provider if FALLBACK selected.

        Returns:
            ``(provider, identity)``: the swapped-in provider plus the
            identity copy carrying the fallback provider name, or the
            original pair when no swap was needed.

        Raises:
            QuotaExhaustedError: If FALLBACK selected a provider but
                no ``provider_registry`` is wired, or the registry
                does not know the fallback provider name.
        """
        effective = preflight.effective_provider
        if effective is None or effective == identity.model.provider:
            return provider, identity

        original = identity.model.provider
        new_provider = self._resolve_fallback_provider(effective, original=original)
        logger.info(
            DEGRADATION_PROVIDER_SWAPPED,
            original_provider=original,
            fallback_provider=effective,
            result="success",
        )
        new_identity = identity.model_copy(
            update={
                "model": identity.model.model_copy(
                    update={"provider": effective},
                ),
            },
        )
        return new_provider, new_identity

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
