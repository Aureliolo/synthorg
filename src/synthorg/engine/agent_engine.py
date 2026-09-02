# module-kind: complex_service
"""Agent engine -- top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

from contextlib import ExitStack
from typing import TYPE_CHECKING, override

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import run_identity_scope
from synthorg.core.types import NotBlankStr
from synthorg.engine._agent_engine_run import AgentEngineRunMixin
from synthorg.engine._stream_progress import (
    make_turn_observer,
    publish_run_started,
    publish_run_terminated,
)
from synthorg.engine._validation import (
    validate_agent,
    validate_run_inputs,
    validate_task,
    validate_task_metadata,
)
from synthorg.engine.agent_engine_chat_action import AgentEngineChatActionMixin
from synthorg.engine.agent_engine_context import (
    AgentEngineContextMixin,
    MemoryContextInputs,
    RunExecutionDeps,
)
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.agent_engine_factories import AgentEngineFactoriesMixin
from synthorg.engine.agent_engine_post_exec import AgentEnginePostExecMixin
from synthorg.engine.agent_engine_recovery import AgentEngineRecoveryMixin
from synthorg.engine.agent_engine_resume import AgentEngineResumeMixin
from synthorg.engine.agent_engine_stakes_errors import AgentEngineStakesErrorsMixin
from synthorg.engine.agent_execute_request import AgentExecuteRequest
from synthorg.engine.agent_state_recording import (
    compose_turn_observers,
    make_runtime_state_observer,
    mark_agent_running,
    release_agent_row,
)
from synthorg.engine.artifacts.baseline_scope import (
    capture_run_baseline,
    run_baseline_scope,
)
from synthorg.engine.autonomy_seam import AutonomyResolution
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
from synthorg.engine.dependencies import (
    EngineBudget,
    EngineCore,
    EngineDependencies,
    EngineGovernance,
    EngineLoopControls,
    EngineMemory,
    EngineObservability,
    EngineOrg,
    EngineRecovery,
    EngineRouting,
    EngineTooling,
)
from synthorg.engine.errors import (
    ExecutionStateError,
    ProjectNotFoundError,
)
from synthorg.engine.loop_budget_signal import resolve_budget_signal_config
from synthorg.engine.loop_empty_run import resolve_produce_early_percent
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.mcp_tool_retrieval import task_brief_text
from synthorg.engine.post_execution.rework_settlement import (
    ScoredRun,
    resolve_rework_bound,
    rework_continuation,
    settle_unresolved_rework,
)
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.wiring_summary import EngineWiringSummary
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.correlation import correlation_scope
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
)
from synthorg.observability.tracing.instrumentation import get_tracer
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.core.task import Task
    from synthorg.engine.coordination.attribution import (
        CoordinationResultWithAttribution,
    )
    from synthorg.engine.coordination.models import CoordinationContext
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.delegation.protocol import SubAgentRunner
    from synthorg.engine.loop_protocol import (
        ExecutionLoop,
    )
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class AgentEngine(
    AgentEngineChatActionMixin,
    AgentEngineContextMixin,
    AgentEngineErrorsMixin,
    AgentEngineFactoriesMixin,
    AgentEnginePostExecMixin,
    AgentEngineRecoveryMixin,
    AgentEngineResumeMixin,
    AgentEngineRunMixin,
    AgentEngineStakesErrorsMixin,
):
    """Top-level orchestrator for agent execution."""

    def __init__(self, deps: EngineDependencies) -> None:
        """Wire an engine from a complete dependency declaration.

        Args:
            deps: Every collaborator this engine runs with, declared in
                full. There is no partial form: a subsystem this
                deployment does not run is written ``None`` in its own
                bundle, which is a decision a reader can see and a
                reviewer can question, where an omitted keyword was a
                decision nobody made.
        """
        self._deps = deps
        self._bind_core(deps.core)
        self._bind_routing(deps.routing)
        self._bind_governance(deps.governance)
        self._bind_loop_controls(deps.loop_controls)
        self._bind_memory(deps.memory, deps.core.provider)
        self._bind_org(deps.org)
        self._bind_tooling(deps.tooling)
        self._bind_observability(deps.observability)
        self._bind_recovery(deps.recovery)
        self._bind_budget(deps.budget)
        self._clarification_enabled = deps.behaviour.clarification_enabled
        self._scoping_enabled = deps.behaviour.scoping_enabled
        # Ordered: the gate reads the governance bundle, and the loop reads
        # the gate alongside every other in-flight control.
        self._approval_gate = self._make_approval_gate()
        self._loop: ExecutionLoop = (
            deps.core.execution_loop or self._make_default_loop()
        )
        # Bound after construction by the boot path (the resolver reads the
        # per-agent level and the initiative mode, both of which the worker
        # layer owns); see ``set_autonomy_resolution``.
        self._autonomy_resolution: AutonomyResolution | None = None
        logger.debug(EXECUTION_ENGINE_CREATED, **self.wiring.log_fields())

    @property
    def wiring(self) -> EngineWiringSummary:
        """What this engine was constructed with, as one readable record.

        The same facts the creation event logs, held as a value so a harness
        measuring this engine can ask what it measured rather than infer it
        from a log line it may not have captured.

        Returns:
            The summary, with the tool surface of the last built invoker.
        """
        # The loop controls reach only a loop the engine built: an injected
        # loop was constructed elsewhere with whatever it was given, so a
        # detector or a callback bound here is held and never consulted, and
        # a summary reporting it present would be the lie this record exists
        # to make impossible.
        reaches_loop = self._deps.core.execution_loop is None
        detector = self._stagnation_detector if reaches_loop else None
        return EngineWiringSummary(
            loop_type=self._loop.get_loop_type(),
            has_tool_registry=self._tool_registry is not None,
            has_cost_tracker=self._cost_tracker is not None,
            has_budget_enforcer=self._budget_enforcer is not None,
            has_coordinator=self._coordinator is not None,
            has_compaction_callback=(
                reaches_loop and self._compaction_callback is not None
            ),
            has_stagnation_detector=detector is not None,
            stagnation_strategy=(
                detector.get_detector_type() if detector is not None else None
            ),
            has_review_pipeline=self._review_pipeline is not None,
            has_memory_backend=self._memory_backend is not None,
            has_sub_agent_runner=self._sub_agent_runner is not None,
            has_approval_gate=reaches_loop and self._approval_gate is not None,
            has_policy_engine=self._policy_engine is not None,
            cost_tracker=self._cost_tracker,
        )

    def _bind_core(self, core: EngineCore) -> None:
        """Bind the provider, the clock, the tools and the loop seam."""
        self._provider = core.provider
        self._clock: Clock = core.clock
        self._config_resolver = core.config_resolver
        self._tool_registry = core.tool_registry
        self._shutdown_checker = core.shutdown_checker

    def _bind_routing(self, routing: EngineRouting) -> None:
        """Bind how an agent's own bound pair resolves to a driver."""
        self._provider_registry = routing.provider_registry
        self._provider_configs = routing.provider_configs
        self._model_resolver = routing.model_resolver

    def _bind_governance(self, governance: EngineGovernance) -> None:
        """Bind approval, policy, audit and the review gates."""
        self._policy_engine = governance.policy_engine
        self._security_config = governance.security_config
        self._security_config_provider = governance.security_config_provider
        self._policy_evaluation_mode = (
            governance.security_config.policy_engine.evaluation_mode
            if governance.security_config is not None
            else "log_only"
        )
        self._audit_log = governance.audit_log
        self._approval_store = governance.approval_store
        # The boot path constructs one ApprovalGate (backed by the
        # persistence ParkedContextRepository) and declares it so the engine
        # parks and the /approvals controller resumes on the same gate. When
        # declared absent the factory builds a gate from the engine's own
        # collaborators.
        self._injected_approval_gate = governance.approval_gate
        self._parked_context_repo = governance.parked_context_repo
        self._approval_interrupt_timeout_seconds = (
            governance.approval_interrupt_timeout_seconds
        )
        self._review_gate = governance.review_gate
        self._review_pipeline = governance.review_pipeline

    def _bind_loop_controls(self, controls: EngineLoopControls) -> None:
        """Bind what the execution loop consults at a turn boundary."""
        self._stagnation_detector = controls.stagnation_detector
        self._compaction_callback = controls.compaction_callback
        self._step_classifier = controls.step_classifier
        self._steering_inbox = controls.steering_inbox
        self._background_job_watcher = controls.background_job_watcher

    def _bind_memory(self, memory: EngineMemory, provider: CompletionProvider) -> None:
        """Bind recall, and the write side that makes a second run cheaper."""
        self._memory_backend = memory.memory_backend
        self._memory_injection_strategy_provider = (
            memory.memory_injection_strategy_provider
        )
        self._ontology_injection_strategy = memory.ontology_injection_strategy
        self._procedural_memory_config = memory.procedural_memory_config
        self._capture_strategy = memory.capture_strategy
        self._distillation_capture_enabled = memory.distillation_capture_enabled
        self._procedural_proposer: ProceduralMemoryProposer | None = None
        # Constructed regardless of ``enabled`` so the switch stays live: the
        # post-execution hook re-resolves it per capture, and constructing a
        # proposer costs nothing until a capture actually dispatches.
        if memory.procedural_memory_config is not None and (
            memory.memory_backend is not None
        ):
            from synthorg.memory.procedural.proposer import (  # noqa: PLC0415
                ProceduralMemoryProposer,
            )

            self._procedural_proposer = ProceduralMemoryProposer(
                provider=provider,
                config=memory.procedural_memory_config,
            )

    def _bind_org(self, org: EngineOrg) -> None:
        """Bind the roster, the board and the MCP surface."""
        self._agent_registry = org.agent_registry
        self._capability = org.capability
        self._task_engine = org.task_engine
        self._project_repo = org.project_repo
        self._coordinator = org.coordinator
        self._evolution_service = org.evolution_service
        # Agent -> SynthOrg-MCP self-consumer: when wired, the tool-invoker
        # factory adds trust-scoped SynthOrg MCP tools to the agent's
        # registry. ``None`` (mode DISABLED) is a no-op.
        self._mcp_self_consumer = org.mcp_self_consumer
        # Blocking-delegation runner dispatches child runs back through this
        # same engine (``AgentEngine.run`` holds no per-run instance state, so
        # the nested call is re-entrant). Wired only when both the task engine
        # and the agent registry are present; ``None`` disables delegation.
        self._sub_agent_runner: SubAgentRunner | None = None
        if org.task_engine is not None and org.agent_registry is not None:
            from synthorg.engine.delegation.runner import (  # noqa: PLC0415
                InProcessSubAgentRunner,
            )

            self._sub_agent_runner = InProcessSubAgentRunner(
                engine=self,
                task_engine=org.task_engine,
                agent_registry=org.agent_registry,
            )

    def _bind_tooling(self, tooling: EngineTooling) -> None:
        """Bind the seams that extend the base tool registry per task."""
        self._external_api_runtime = tooling.external_api_runtime
        self._connection_tool_runtimes = tooling.connection_tool_runtimes
        self._tool_invocation_tracker = tooling.tool_invocation_tracker
        self._brain_tool_factory_provider = tooling.brain_tool_factory_provider
        self._knowledge_tool_factory_provider = tooling.knowledge_tool_factory_provider
        self._docs_tool_factory_provider = tooling.docs_tool_factory_provider
        self._research_tool_factory_provider = tooling.research_tool_factory_provider
        self._structure_map_tool_factory_provider = (
            tooling.structure_map_tool_factory_provider
        )

    def _bind_observability(self, observability: EngineObservability) -> None:
        """Bind what watches a run from outside it."""
        self._event_stream_hub = observability.event_stream_hub
        self._event_reader = observability.event_reader
        self._interrupt_store = observability.interrupt_store
        self._flight_recorder_sink = observability.flight_recorder_sink
        self._agent_state_repository_provider = (
            observability.agent_state_repository_provider
        )
        self._classification_sinks = observability.classification_sinks
        self._error_taxonomy_config = observability.error_taxonomy_config
        self._agent_middleware_chain = observability.agent_middleware_chain

    def _bind_recovery(self, recovery: EngineRecovery) -> None:
        """Bind what happens when a run does not finish."""
        self._recovery_strategy = recovery.recovery_strategy
        self._run_probe = recovery.run_probe
        self._checkpointing = recovery.checkpointing

    def _bind_budget(self, budget: EngineBudget) -> None:
        """Bind what measures and bounds spend."""
        self._budget_enforcer = budget.budget_enforcer
        self._cost_tracker: CostTrackerProtocol | None = budget.effective_tracker
        self._cost_forecast_repo = budget.cost_forecast_repo
        self._coordination_metrics_collector = budget.coordination_metrics_collector

    def set_autonomy_resolution(self, resolution: AutonomyResolution) -> None:
        """Bind the one resolver every dispatch path asks for autonomy.

        Called by the boot path once the worker execution service exists.
        Until it is bound, a caller that supplies no autonomy runs
        degraded, which is what a coordinated wave did permanently.

        Args:
            resolution: The single owner of "what autonomy governs this
                run", asked whenever :meth:`run` is called without one.
        """
        self._autonomy_resolution = resolution

    async def _effective_autonomy_for(
        self,
        identity: AgentIdentity,
        *,
        task_id: str,
        project_id: NotBlankStr | None,
    ) -> EffectiveAutonomy | None:
        """Ask the bound resolver what governs this run.

        Returns:
            The resolved autonomy, or ``None`` when nothing is bound.
        """
        if self._autonomy_resolution is None:
            return None
        return await self._autonomy_resolution(
            identity, task_id=task_id, project_id=project_id
        )

    @property
    def coordinator(self) -> MultiAgentCoordinator | None:
        """Return the multi-agent coordinator, or ``None`` if not configured."""
        return self._coordinator

    @property
    def has_mcp_self_consumer(self) -> bool:
        """Whether trust-scoped SynthOrg MCP tools are wired into agents.

        Gates the direct-MCP conversational actor: with no self-consumer
        an acting agent has no MCP tools, so ``/meta/chat/act`` 503s.
        """
        return self._mcp_self_consumer is not None

    async def coordinate(
        self,
        context: CoordinationContext,
    ) -> CoordinationResultWithAttribution:
        """Delegate to the multi-agent coordinator.

        Returns:
            The :class:`CoordinationResultWithAttribution` from the
            coordinator's ``coordinate()`` call.

        Raises:
            ExecutionStateError: If no coordinator was configured.
        """
        if self._coordinator is None:
            msg = "No coordinator configured for multi-agent dispatch"
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                error=msg,
            )
            raise ExecutionStateError(msg)
        return await self._coordinator.coordinate(context)

    async def project_background_failure(self, *, task_id: str, agent_id: str) -> None:
        """Project a terminal RUN_ERROR for a run that failed before the loop.

        A backgrounded conversational run can fail in the pipeline spine
        (project resolution, decomposition, assignment) before the execution
        loop ever runs to publish its own terminal frame, leaving a dashboard
        subscribed to the task's SSE stream hung on "Working". Called by the
        worker's background wrapper on such a failure so the operator sees the
        run end. No-op when no event-stream hub is wired.
        """
        hub = self._event_stream_hub
        if hub is None:
            return
        await publish_run_terminated(
            hub, task_id=task_id, agent_id=agent_id, reason=TerminationReason.ERROR
        )

    async def run(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        completion_config: CompletionConfig | None = None,
        max_turns: int | None = None,
        memory_messages: tuple[ChatMessage, ...] = (),
        timeout_seconds: float | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        resume_execution_id: str | None = None,
    ) -> AgentRunResult:
        """Execute an agent on a task.

        Returns:
            The :class:`AgentRunResult` from the loop, with cost
            tracking, post-execution transitions, and recovery /
            checkpoint resume applied.

        Raises:
            MemoryError: Re-raised after logging from the explicit
                log-and-raise critical-error path (the engine surfaces
                non-recoverable interpreter signals to the worker).
            RecursionError: Same path as ``MemoryError``.
            ProjectNotFoundError: From project validation when the
                task references a missing project.
        """
        agent_id = str(identity.id)
        task_id = str(task.id)
        if max_turns is None:
            max_turns = await self._resolve_max_turns(
                agent_id=agent_id, task_id=task_id
            )

        validate_run_inputs(
            agent_id=agent_id,
            task_id=task_id,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        validate_agent(identity, agent_id)
        validate_task(task, agent_id, task_id)
        validate_task_metadata(task, agent_id, task_id)

        with (
            correlation_scope(
                agent_id=agent_id,
                task_id=task_id,
                project_id=task.project,
            ),
            ExitStack() as run_scopes,
        ):
            start = self._clock.monotonic()
            ctx: AgentContext | None = None
            system_prompt: SystemPrompt | None = None
            provider: CompletionProvider = self._provider
            _project_budget: float = 0.0
            try:
                # Entered here rather than in the `with` header above so a
                # capture failure lands inside the fatal-error boundary. The
                # probe deliberately propagates everything that is not storage
                # I/O, and outside the boundary that left the run with no
                # terminal projection at all: no FAILED, nothing to replan.
                run_scopes.enter_context(
                    run_baseline_scope(
                        await capture_run_baseline(
                            self._run_probe,
                            project_id=task.project,
                            expected=task.artifacts_expected,
                        )
                    )
                )
                # Dispatch to the provider serving this agent's own model,
                # which nothing downstream re-points; a registry miss (agent
                # pinned to an unregistered provider) fails the run here
                # rather than mis-dispatching to the engine default.
                provider = self._dispatch_client_for(identity, self._provider)
                if effective_autonomy is None:
                    effective_autonomy = await self._effective_autonomy_for(
                        identity, task_id=task_id, project_id=task.project
                    )
                logger.info(
                    EXECUTION_ENGINE_START,
                    agent_id=agent_id,
                    task_id=task_id,
                    loop_type=self._loop.get_loop_type(),
                    max_turns=max_turns,
                )

                provider, identity, completion_config = await self._bind_run(
                    identity=identity,
                    task=task,
                    provider=provider,
                    completion_config=completion_config,
                )

                if self._project_repo is not None:
                    _project_budget = await self._validate_project(
                        task=task,
                        agent_id=agent_id,
                        task_id=task_id,
                    )
                elif task.project:
                    # Fail loud for a work task (aborts to the fatal-error
                    # boundary, which terminates the task FAILED) rather than
                    # running it unvalidated against an unconfigured repo.
                    self._reject_unconfigured_project_repo(
                        task=task,
                        agent_id=agent_id,
                        task_id=task_id,
                    )

                replay_ctx: AgentContext | None = None
                if resume_execution_id is not None:
                    replay_ctx = await self._replay_session(
                        resume_execution_id=resume_execution_id,
                        identity=identity,
                        task=task,
                        max_turns=max_turns,
                    )

                # Once for the whole task. The reconciler can replace a
                # backend between these two calls, and a task whose tools
                # came from one strategy while its context came from another
                # would recall against a backend its tools do not write to.
                memory_strategy = self._resolve_memory_strategy()
                tool_invoker = self._make_tool_invoker(
                    identity,
                    task_id=task_id,
                    effective_autonomy=effective_autonomy,
                    project_id=task.project,
                    memory_strategy=memory_strategy,
                    retrieval_query=task_brief_text(task),
                )
                # Built before the prompt, not inside the execution span, so
                # the ceilings it publishes can be stamped on the context the
                # prompt is built from: a resolution inside the span would be
                # too late to declare, and a second one would be a second
                # owner of the same ceiling.
                budget_checker = await self._build_budget_checker(
                    task,
                    agent_id,
                    project_id=task.project,
                    project_budget=_project_budget,
                )
                ctx, system_prompt = await self._prepare_context(
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    max_turns=max_turns,
                    memory=MemoryContextInputs(
                        messages=memory_messages, strategy=memory_strategy
                    ),
                    execution=RunExecutionDeps(
                        provider=provider,
                        budget_checker=budget_checker,
                        tool_invoker=tool_invoker,
                    ),
                    effective_autonomy=effective_autonomy,
                )
                if replay_ctx is not None:
                    ctx = self._merge_replayed(ctx, replay_ctx)
                # Bind the run identity (same execution_id flight frames
                # carry) so capture leaves tag records the receipt joins on.
                with run_identity_scope(
                    execution_id=ctx.execution_id,
                    task_id=task_id,
                    project_id=task.project,
                ):
                    return await self._execute(
                        AgentExecuteRequest(
                            identity=identity,
                            task=task,
                            agent_id=agent_id,
                            task_id=task_id,
                            completion_config=completion_config,
                            ctx=ctx,
                            system_prompt=system_prompt,
                            start=start,
                            timeout_seconds=timeout_seconds,
                            tool_invoker=tool_invoker,
                            effective_autonomy=effective_autonomy,
                            provider=provider,
                            budget_checker=budget_checker,
                        )
                    )
            except (MemoryError, RecursionError) as exc:
                log_exception_redacted(
                    logger,
                    EXECUTION_ENGINE_ERROR,
                    exc,
                    agent_id=agent_id,
                    task_id=task_id,
                    reason="non-recoverable error in run()",
                )
                raise
            except ProjectNotFoundError:
                raise
            except BudgetExhaustedError as exc:
                budget_result = await self._handle_budget_error(
                    exc=exc,
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    duration_seconds=self._clock.monotonic() - start,
                    ctx=ctx,
                    system_prompt=system_prompt,
                )
                # Project the terminal the budget handler actually selected: a
                # parked hard-ceiling crossing is PARKED (silent -- the pause
                # surfaces via the approval-interrupt projection), a plain
                # controlled stop is BUDGET_EXHAUSTED (RUN_ERROR). The inner
                # handler skips RUN_ERROR for a budget error precisely so a
                # parked run is never projected as failed.
                budget_hub = self._event_stream_hub
                if budget_hub is not None:
                    await publish_run_terminated(
                        budget_hub,
                        task_id=task_id,
                        agent_id=agent_id,
                        reason=budget_result.execution_result.termination_reason,
                    )
                return budget_result
            except StakesModelUnavailableError as exc:
                return await self._handle_stakes_unavailable(
                    exc=exc,
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    duration_seconds=self._clock.monotonic() - start,
                    ctx=ctx,
                    system_prompt=system_prompt,
                    completion_config=completion_config,
                    effective_autonomy=effective_autonomy,
                    provider=provider,
                )
            except Exception as exc:  # noqa: BLE001 -- engine fatal-error boundary
                # lint-allow: swallow-ok -- fatal-error boundary returns FAILED
                return await self._handle_fatal_error(
                    exc=exc,
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    duration_seconds=self._clock.monotonic() - start,
                    ctx=ctx,
                    system_prompt=system_prompt,
                    completion_config=completion_config,
                    effective_autonomy=effective_autonomy,
                    provider=provider,
                )

    @override
    async def _execute(self, request: AgentExecuteRequest) -> AgentRunResult:
        """Run execution loop, record costs, apply transitions, and build result.

        Returns:
            The :class:`AgentRunResult` carrying the loop outcome,
            recovery decision (when one fired), and post-execution
            telemetry for the orchestrator.

        Raises:
            asyncio.CancelledError: Re-raised after the live-state release has
                landed, so a stopping process still frees the agent's row.
        """
        agent_id = request.agent_id
        try:
            # Before the loop, not from its first turn report: a turn that
            # finishes the run returns the result instead of reporting, so a
            # single-turn dispatch would otherwise never write a row, and a
            # longer one would have none until its first turn ended.
            await mark_agent_running(
                repository_provider=self._agent_state_repository_provider,
                context=request.ctx,
                currency=resolve_tracker_currency(self._cost_tracker),
                clock=self._clock,
            )
            return await self._execute_span(request)
        finally:
            # In a finally because a run that died still has to stop reading
            # as busy: ``get_active`` is the query the live view is built on,
            # and a row left EXECUTING makes a finished agent look occupied
            # for the life of the process. Naming the execution keeps the
            # clear from blanking a sibling dispatch's live row: the row is
            # keyed by agent, and one agent can hold two.
            #
            # Through the shared release, which owns the shield AND the
            # second await that stops a cancellation abandoning the write.
            await release_agent_row(
                repository_provider=self._agent_state_repository_provider,
                agent_id=agent_id,
                execution_id=request.ctx.execution_id,
                currency=resolve_tracker_currency(self._cost_tracker),
                clock=self._clock,
            )

    async def _execute_span(self, request: AgentExecuteRequest) -> AgentRunResult:
        """Run the dispatch inside its own ``agent.execution`` span.

        Returns:
            The finished :class:`AgentRunResult`.
        """
        identity = request.identity
        task = request.task
        agent_id = request.agent_id
        task_id = request.task_id
        completion_config = request.completion_config
        ctx = request.ctx
        system_prompt = request.system_prompt
        start = request.start
        timeout_seconds = request.timeout_seconds
        tool_invoker = request.tool_invoker
        effective_autonomy = request.effective_autonomy
        provider = request.provider
        budget_checker = request.budget_checker
        with _tracer.start_as_current_span(
            "agent.execution",
            attributes={
                "agent.id": agent_id,
                "task.id": task_id,
                "agent.status": identity.status.value,
            },
        ) as span:
            logger.debug(
                EXECUTION_ENGINE_PROMPT_BUILT,
                agent_id=agent_id,
                task_id=task_id,
                estimated_tokens=system_prompt.estimated_tokens,
            )

            loop = self._loop
            # Project live execution progress onto the AG-UI stream (keyed by
            # session_id == task_id) so the dashboard can render the run
            # instead of a silent gap between propose and review. All
            # best-effort: a missing hub disables projection entirely.
            hub = self._event_stream_hub
            # Two consumers of one per-turn report: the AG-UI projection the
            # dashboard renders, and the live runtime-state row the cockpit
            # reads for a run still in flight. The loop reports once; how many
            # things listen is decided here.
            turn_observer = compose_turn_observers(
                make_turn_observer(hub, task_id=task_id, agent_id=agent_id)
                if hub is not None
                else None,
                make_runtime_state_observer(
                    repository_provider=self._agent_state_repository_provider,
                    currency=resolve_tracker_currency(self._cost_tracker),
                    clock=self._clock,
                ),
            )
            if hub is not None:
                await publish_run_started(hub, task_id=task_id, agent_id=agent_id)
            # Stream the per-turn LLM calls (with mid-turn cancellation and
            # steer-interrupt) when the operator setting is on and the model
            # supports it; else the loop uses the non-streaming call path.
            streaming_enabled = await self._resolve_streaming_enabled(
                provider or self._provider, identity, task_id=task_id
            )
            budget_signal_config = await resolve_budget_signal_config(
                self._config_resolver
            )
            produce_early_percent = await resolve_produce_early_percent(
                self._config_resolver
            )
            # before/after_agent fire around the loop run (no-op when unwired);
            # after_agent is guaranteed in a finally inside the helper so a
            # loop timeout/exception cannot skip the end-of-run cleanup seam.
            # Deferred to avoid an engine -> engine.middleware module-level edge.
            from synthorg.engine import _agent_middleware_run as _amr  # noqa: PLC0415

            async def _run_loop(run_ctx: AgentContext) -> ExecutionResult:
                """Run the execution loop for the middleware envelope.

                Returns:
                    The :class:`ExecutionResult` from the timed run loop.
                """
                return await self._run_loop_with_timeout(
                    loop=loop,
                    ctx=run_ctx,
                    agent_id=agent_id,
                    task_id=task_id,
                    completion_config=completion_config,
                    budget_checker=budget_checker,
                    tool_invoker=tool_invoker,
                    start=start,
                    timeout_seconds=timeout_seconds,
                    provider=provider or self._provider,
                    turn_observer=turn_observer,
                    streaming_enabled=streaming_enabled,
                    budget_signal_config=budget_signal_config,
                    produce_early_percent=produce_early_percent,
                )

            # A review that returns REWORK means "run this again", and this
            # dispatch is the only thing holding a loop that can: the
            # coordination wave has returned and nothing polls IN_PROGRESS.
            # Bounded, and the gate's own reason is handed back each round.
            rework_rounds = 0
            max_rework_rounds = await resolve_rework_bound(self._config_resolver)
            scored: ScoredRun | None = None

            async def _run_rounds(start_ctx: AgentContext) -> ExecutionResult:
                """Run, score, and re-run while the review sends the work back.

                Inside the middleware envelope rather than around it: the
                before/after_agent pair is the once-per-run seam, and firing
                it per round would re-run authority checks against a
                conversation that already carries their effects.

                Returns:
                    The last scored attempt's :class:`ExecutionResult`.
                """
                nonlocal rework_rounds, scored
                run_ctx = start_ctx
                # lint-allow: long-running-loop-kill-switch -- rounds bounded per run
                while True:
                    result = await _run_loop(run_ctx)
                    attempt = await self._post_execution_pipeline(
                        result,
                        identity,
                        agent_id,
                        task_id,
                        completion_config=completion_config,
                        effective_autonomy=effective_autonomy,
                        provider=provider or self._provider,
                        project_id=task.project,
                    )
                    scored = attempt
                    resumed = rework_continuation(
                        attempt.result,
                        rounds_taken=rework_rounds,
                        max_rounds=max_rework_rounds,
                    )
                    if resumed is None:
                        return attempt.result
                    run_ctx = resumed
                    rework_rounds += 1

            try:
                execution_result: ExecutionResult = (
                    await _amr.run_with_agent_middleware(
                        self._agent_middleware_chain,
                        loop_runner=_run_rounds,
                        ctx=ctx,
                        identity=identity,
                        task=task,
                        agent_id=agent_id,
                        task_id=task_id,
                        effective_autonomy=effective_autonomy,
                    )
                )
            except Exception as exc:
                # Let a non-recoverable interpreter signal propagate
                # immediately, before the terminal publish below
                # allocates/serialises a frame that could mask the
                # original critical under memory exhaustion.
                reraise_critical(exc)
                # A fatal error skips the normal terminal projection below,
                # so the live panel would hang on "Working" forever:
                # project RUN_ERROR before the exception propagates. A
                # BudgetExhaustedError is excluded -- the outer budget
                # handler converts it to a PARKED approval pause (projected
                # separately) or a BUDGET_EXHAUSTED stop and projects that
                # terminal itself, so a RUN_ERROR here would show a paused
                # run as failed. Cancellation is not caught: a
                # shutdown/disconnect resumes and must not be reported as
                # failed.
                if hub is not None and not isinstance(exc, BudgetExhaustedError):
                    await publish_run_terminated(
                        hub,
                        task_id=task_id,
                        agent_id=agent_id,
                        reason=TerminationReason.ERROR,
                    )
                raise
            try:
                execution_result = await settle_unresolved_rework(
                    execution_result,
                    agent_id=agent_id,
                    task_id=task_id,
                    rounds_taken=rework_rounds,
                    task_engine=self._task_engine,
                    approval_store=self._approval_store,
                )
            except Exception as exc:
                reraise_critical(exc)
                # Settlement is what drives an uncleared review to FAILED, so
                # a raise here ends the run as surely as an execution fault
                # does and needs the same terminal, or the live panel hangs
                # on "Working".
                if hub is not None:
                    await publish_run_terminated(
                        hub,
                        task_id=task_id,
                        agent_id=agent_id,
                        reason=TerminationReason.ERROR,
                    )
                raise
            # Published once the run has actually terminated, and after
            # settlement rather than before it: an unresolved rework lands the
            # task FAILED there, so publishing first announced a successful
            # terminal for a run being failed underneath it. Announcing a
            # terminal per round told the live panel the run was over and then
            # showed the agent working again with nothing in between.
            if hub is not None:
                await publish_run_terminated(
                    hub,
                    task_id=task_id,
                    agent_id=agent_id,
                    reason=execution_result.termination_reason,
                )
            if scored is not None:
                await self._finalise_run(
                    scored._replace(result=execution_result), agent_id, task_id
                )

            # Read from the post-execution context: ``ctx`` is the
            # pre-loop snapshot and copy-on-write contexts inside the
            # loop don't mutate it, so logging ``ctx.turn_count`` here
            # would always emit the starting value (typically 0).
            span.set_attribute(
                "turn.count",
                execution_result.context.turn_count,
            )
            return self._build_and_log_result(
                execution_result,
                system_prompt,
                start,
                agent_id,
                task_id,
                # The pair the operator bound to this agent, which nothing in
                # the run rewrites, so it is what actually produced the
                # output.
                bound_model=identity.model,
            )
