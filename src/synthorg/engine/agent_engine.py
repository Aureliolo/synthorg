# module-kind: complex_service
"""Agent engine -- top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from typing import TYPE_CHECKING, Literal, TypedDict, override

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import run_identity_scope
from synthorg.core.types import NotBlankStr
from synthorg.engine._agent_engine_run import AgentEngineRunMixin
from synthorg.engine._agent_engine_types import (
    BrainToolFactoryProvider,
    DocsToolFactoryProvider,
    KnowledgeToolFactoryProvider,
    ResearchToolFactoryProvider,
    StructureMapToolFactoryProvider,
)
from synthorg.engine._ceiling_sync import ceiling_synced_task
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
from synthorg.engine.agent_engine_context import AgentEngineContextMixin
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.agent_engine_factories import AgentEngineFactoriesMixin
from synthorg.engine.agent_engine_post_exec import AgentEnginePostExecMixin
from synthorg.engine.agent_engine_recovery import AgentEngineRecoveryMixin
from synthorg.engine.agent_engine_resume import AgentEngineResumeMixin
from synthorg.engine.agent_engine_stakes_errors import AgentEngineStakesErrorsMixin
from synthorg.engine.agent_execute_request import AgentExecuteRequest
from synthorg.engine.artifacts.baseline_scope import (
    artifact_baseline_scope,
    capture_run_baseline,
)
from synthorg.engine.artifacts.expected_artifact_check import ExpectedArtifactProbe
from synthorg.engine.autonomy_seam import AutonomyResolution
from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import (
    ExecutionStateError,
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
)
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    make_budget_checker,
)
from synthorg.engine.loop_selector import AutoLoopConfig
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.correlation import correlation_scope
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_LOOP_WIRING_WARNING,
)
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
)
from synthorg.observability.events.session import (
    SESSION_REPLAY_LOW_COMPLETENESS,
)
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_BUDGET_OVERRODE,
)
from synthorg.observability.tracing.instrumentation import get_tracer
from synthorg.providers.models import ChatMessage
from synthorg.security.audit import AuditLog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.coordination_collector import CoordinationMetricsCollector
    from synthorg.budget.coordination_config import ErrorTaxonomyConfig
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.communication.event_stream.interrupt import InterruptStore
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.core.task import Task
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.classification.protocol import ClassificationSink
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.coordination.attribution import (
        CoordinationResultWithAttribution,
    )
    from synthorg.engine.coordination.models import CoordinationContext
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.delegation.protocol import SubAgentRunner
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.flight_recording import FlightRecorderSink
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
        ShutdownChecker,
    )
    from synthorg.engine.mcp_self_consumer import MCPSelfConsumerProvider
    from synthorg.engine.middleware.protocol import AgentMiddlewareChain
    from synthorg.engine.openhands.config import (
        OpenHandsLoopConfig,
        OpenHandsLoopDeps,
    )
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.engine.quality.classifier import StepQualityClassifier
    from synthorg.engine.recovery import RecoveryStrategy
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.engine.review_gate import ReviewGateService
    from synthorg.engine.routing_policy.router import StakesRouter
    from synthorg.engine.session import EventReader
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.hr.registry_protocol import AgentRegistryProtocol
    from synthorg.memory.injection import MemoryInjectionStrategy
    from synthorg.memory.procedural.capture.protocol import CaptureStrategy
    from synthorg.memory.procedural.models import ProceduralMemoryConfig
    from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.ontology.injection.protocol import OntologyInjectionStrategy
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
    from synthorg.persistence.parked_context_protocol import ParkedContextRepository
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.providers.routing.resolver import ModelResolver
    from synthorg.security.config import SecurityConfig
    from synthorg.security.policy_engine.protocol import PolicyEngine
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.tools.chat._runtime import ChatToolsRuntime
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.forge._runtime import ForgeToolsRuntime
    from synthorg.tools.invocation_tracker import ToolInvocationTracker
    from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_REPLAY_LOW_COMPLETENESS_THRESHOLD: float = 0.5
"""Log a warning when session replay completeness is below this."""

_DEFAULT_RECOVERY_STRATEGY = FailAndReassignStrategy()
"""Module-level default instance for the recovery strategy."""


class PersonalityTrimPayload(TypedDict):
    """Structured payload forwarded to :data:`PersonalityTrimNotifier` callbacks."""

    agent_id: NotBlankStr
    agent_name: NotBlankStr
    task_id: NotBlankStr
    before_tokens: int
    after_tokens: int
    max_tokens: int
    trim_tier: Literal[1, 2, 3]
    budget_met: bool


type PersonalityTrimNotifier = Callable[[PersonalityTrimPayload], Awaitable[None]]
"""Async callback invoked when an agent's personality section is trimmed."""


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

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider: CompletionProvider,
        execution_loop: ExecutionLoop | None = None,
        tool_registry: ToolRegistry | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        recovery_strategy: RecoveryStrategy | None = _DEFAULT_RECOVERY_STRATEGY,
        shutdown_checker: ShutdownChecker | None = None,
        error_taxonomy_config: ErrorTaxonomyConfig | None = None,
        classification_sinks: tuple[ClassificationSink, ...] = (),
        evolution_service: EvolutionService | None = None,
        policy_engine: PolicyEngine | None = None,
        budget_enforcer: BudgetEnforcer | None = None,
        security_config: SecurityConfig | None = None,
        security_config_provider: Callable[[], SecurityConfig | None] | None = None,
        approval_store: ApprovalStoreProtocol | None = None,
        review_gate: ReviewGateService | None = None,
        review_pipeline: ReviewPipeline | None = None,
        artifact_probe: ExpectedArtifactProbe | None = None,
        clarification_enabled: bool = True,
        scoping_enabled: bool = True,
        parked_context_repo: ParkedContextRepository | None = None,
        cost_forecast_repo: CostForecastRepository | None = None,
        approval_gate: ApprovalGate | None = None,
        mcp_self_consumer: MCPSelfConsumerProvider | None = None,
        task_engine: TaskEngine | None = None,
        checkpoint_repo: CheckpointRepository | None = None,
        heartbeat_repo: HeartbeatRepository | None = None,
        checkpoint_config: CheckpointConfig | None = None,
        coordinator: MultiAgentCoordinator | None = None,
        stagnation_detector: StagnationDetector | None = None,
        step_classifier: StepQualityClassifier | None = None,
        steering_inbox: SteeringInbox | None = None,
        auto_loop_config: AutoLoopConfig | None = None,
        openhands_loop_config: OpenHandsLoopConfig | None = None,
        openhands_loop_deps: OpenHandsLoopDeps | None = None,
        compaction_callback: CompactionCallback | None = None,
        provider_registry: ProviderRegistry | None = None,
        provider_configs: Mapping[str, ProviderConfig] | None = None,
        model_resolver: ModelResolver | None = None,
        tool_invocation_tracker: ToolInvocationTracker | None = None,
        memory_injection_strategy: MemoryInjectionStrategy | None = None,
        ontology_injection_strategy: OntologyInjectionStrategy | None = None,
        procedural_memory_config: ProceduralMemoryConfig | None = None,
        capture_strategy: CaptureStrategy | None = None,
        memory_backend: MemoryBackend | None = None,
        distillation_capture_enabled: bool = False,
        config_resolver: ConfigResolver | None = None,
        personality_trim_notifier: PersonalityTrimNotifier | None = None,
        coordination_metrics_collector: CoordinationMetricsCollector | None = None,
        audit_log: AuditLog | None = None,
        project_repo: ProjectRepository | None = None,
        agent_middleware_chain: AgentMiddlewareChain | None = None,
        event_reader: EventReader | None = None,
        event_stream_hub: EventStreamHub | None = None,
        interrupt_store: InterruptStore | None = None,
        approval_interrupt_timeout_seconds: float | None = None,
        external_api_runtime: ExternalApiRuntime | None = None,
        forge_tools_runtime: ForgeToolsRuntime | None = None,
        chat_tools_runtime: ChatToolsRuntime | None = None,
        brain_tool_factory_provider: BrainToolFactoryProvider | None = None,
        knowledge_tool_factory_provider: KnowledgeToolFactoryProvider | None = None,
        docs_tool_factory_provider: DocsToolFactoryProvider | None = None,
        research_tool_factory_provider: ResearchToolFactoryProvider | None = None,
        structure_map_tool_factory_provider: (
            StructureMapToolFactoryProvider | None
        ) = None,
        stakes_router: StakesRouter | None = None,
        agent_registry: AgentRegistryProtocol | None = None,
        flight_recorder_sink: FlightRecorderSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._agent_middleware_chain = agent_middleware_chain
        self._event_reader = event_reader
        self._flight_recorder_sink = flight_recorder_sink
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._event_stream_hub = event_stream_hub
        self._interrupt_store = interrupt_store
        if execution_loop is not None and auto_loop_config is not None:
            msg = "execution_loop and auto_loop_config are mutually exclusive"
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                reason=msg,
            )
            raise ValueError(msg)
        self._provider = provider
        self._provider_registry = provider_registry
        self._provider_configs = provider_configs
        self._model_resolver = model_resolver
        self._approval_store = approval_store
        self._review_gate = review_gate
        self._review_pipeline = review_pipeline
        self._artifact_probe = artifact_probe
        self._clarification_enabled = clarification_enabled
        self._scoping_enabled = scoping_enabled
        self._external_api_runtime = external_api_runtime
        self._forge_tools_runtime = forge_tools_runtime
        self._chat_tools_runtime = chat_tools_runtime
        self._brain_tool_factory_provider = brain_tool_factory_provider
        self._knowledge_tool_factory_provider = knowledge_tool_factory_provider
        self._docs_tool_factory_provider = docs_tool_factory_provider
        self._research_tool_factory_provider = research_tool_factory_provider
        self._structure_map_tool_factory_provider = structure_map_tool_factory_provider
        self._parked_context_repo = parked_context_repo
        self._cost_forecast_repo = cost_forecast_repo
        # The boot path constructs one ApprovalGate (backed by the
        # persistence ParkedContextRepository) and injects it so the
        # engine parks and the /approvals controller resumes on the
        # same gate. When absent (standalone / legacy callers) the
        # factory builds a gate from the engine's own collaborators.
        self._injected_approval_gate = approval_gate
        # Agent -> SynthOrg-MCP self-consumer: when wired, the
        # tool-invoker factory adds trust-scoped SynthOrg MCP tools to
        # the agent's registry. ``None`` (mode DISABLED) is a no-op.
        self._mcp_self_consumer = mcp_self_consumer
        self._approval_interrupt_timeout_seconds = approval_interrupt_timeout_seconds
        self._stakes_router = stakes_router
        self._stagnation_detector = stagnation_detector
        self._step_classifier = step_classifier
        self._steering_inbox = steering_inbox
        self._auto_loop_config = auto_loop_config
        self._openhands_loop_config = openhands_loop_config
        self._openhands_loop_deps = openhands_loop_deps
        self._compaction_callback = compaction_callback
        self._approval_gate = self._make_approval_gate()
        if execution_loop is not None and (
            self._approval_gate is not None
            or self._stagnation_detector is not None
            or self._compaction_callback is not None
        ):
            logger.warning(
                APPROVAL_GATE_LOOP_WIRING_WARNING,
                note=(
                    "execution_loop provided externally -- approval_gate, "
                    "stagnation_detector, and compaction_callback will NOT "
                    "be wired automatically. Configure the loop with "
                    "approval_gate=, stagnation_detector=, and "
                    "compaction_callback= explicitly."
                ),
            )
        self._loop: ExecutionLoop = execution_loop or self._make_default_loop()
        self._tool_registry = tool_registry
        self._budget_enforcer = budget_enforcer
        if (checkpoint_repo is None) != (heartbeat_repo is None):
            msg = (
                "checkpoint_repo and heartbeat_repo must both be "
                "provided or both omitted"
            )
            raise ValueError(msg)
        self._checkpoint_repo = checkpoint_repo
        self._heartbeat_repo = heartbeat_repo
        self._checkpoint_config = checkpoint_config or CheckpointConfig()
        self._cost_tracker: CostTrackerProtocol | None
        if budget_enforcer is not None:
            if (
                cost_tracker is not None
                and cost_tracker is not budget_enforcer.cost_tracker
            ):
                msg = (
                    "cost_tracker must match budget_enforcer.cost_tracker "
                    "when budget_enforcer is provided"
                )
                raise ValueError(msg)
            self._cost_tracker = budget_enforcer.cost_tracker
        else:
            self._cost_tracker = cost_tracker
        self._security_config = security_config
        # When a provider is wired (boot path), the live security config is
        # read through it per request so operator toggles to
        # security.enabled / audit_enabled / post_tool_scanning_enabled /
        # output_scan_policy_type apply without a restart. Tests / direct
        # construction omit it and fall back to the static ``security_config``.
        self._security_config_provider = security_config_provider
        self._task_engine = task_engine
        self._recovery_strategy = recovery_strategy
        self._shutdown_checker = shutdown_checker
        self._error_taxonomy_config = error_taxonomy_config
        self._classification_sinks = classification_sinks
        self._evolution_service = evolution_service
        self._policy_engine = policy_engine
        self._policy_evaluation_mode = (
            security_config.policy_engine.evaluation_mode
            if security_config is not None
            else "log_only"
        )
        self._coordinator = coordinator
        self._tool_invocation_tracker = tool_invocation_tracker
        self._memory_injection_strategy = memory_injection_strategy
        self._ontology_injection_strategy = ontology_injection_strategy
        self._procedural_memory_config = procedural_memory_config
        self._capture_strategy = capture_strategy
        self._memory_backend = memory_backend
        self._distillation_capture_enabled = distillation_capture_enabled
        self._config_resolver = config_resolver
        self._personality_trim_notifier = personality_trim_notifier
        self._coordination_metrics_collector = coordination_metrics_collector
        self._procedural_proposer: ProceduralMemoryProposer | None = None
        # Constructed regardless of ``enabled`` so the switch stays live: the
        # post-execution hook re-resolves it per capture, and constructing a
        # proposer costs nothing until a capture actually dispatches.
        if procedural_memory_config is not None and memory_backend is not None:
            from synthorg.memory.procedural.proposer import (  # noqa: PLC0415
                ProceduralMemoryProposer,
            )

            self._procedural_proposer = ProceduralMemoryProposer(
                provider=provider,
                config=procedural_memory_config,
            )
        self._audit_log = audit_log if audit_log is not None else AuditLog()
        self._project_repo = project_repo
        self._agent_registry = agent_registry
        # Bound after construction by the boot path (the resolver reads the
        # per-agent level and the initiative mode, both of which the worker
        # layer owns); see ``set_autonomy_resolution``.
        self._autonomy_resolution: AutonomyResolution | None = None
        # Blocking-delegation runner dispatches child runs back through this
        # same engine (``AgentEngine.run`` holds no per-run instance state, so
        # the nested call is re-entrant). Wired only when both the task engine
        # and the agent registry are present; ``None`` disables delegation.
        self._sub_agent_runner: SubAgentRunner | None = None
        if task_engine is not None and agent_registry is not None:
            from synthorg.engine.delegation.runner import (  # noqa: PLC0415
                InProcessSubAgentRunner,
            )

            self._sub_agent_runner = InProcessSubAgentRunner(
                engine=self,
                task_engine=task_engine,
                agent_registry=agent_registry,
            )
        logger.debug(
            EXECUTION_ENGINE_CREATED,
            loop_type=(
                "auto"
                if self._auto_loop_config is not None
                else self._loop.get_loop_type()
            ),
            has_tool_registry=self._tool_registry is not None,
            has_cost_tracker=self._cost_tracker is not None,
            has_budget_enforcer=self._budget_enforcer is not None,
            has_coordinator=self._coordinator is not None,
            has_compaction_callback=self._compaction_callback is not None,
            has_openhands_loop_deps=self._openhands_loop_deps is not None,
            has_personality_trim_notifier=self._personality_trim_notifier is not None,
            has_sub_agent_runner=self._sub_agent_runner is not None,
        )

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
            ProjectAgentNotMemberError: From project validation when
                the agent is not a member of the project's team.
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
                    artifact_baseline_scope(
                        await capture_run_baseline(
                            self._artifact_probe,
                            project_id=task.project,
                            expected=task.artifacts_expected,
                        )
                    )
                )
                # Dispatch to the provider serving this agent's own model
                # before stakes routing may re-point it; a registry miss
                # (agent pinned to an unregistered provider) fails the run
                # here rather than mis-dispatching to the engine default.
                provider = self._dispatch_client_for(identity, self._provider)
                if effective_autonomy is None:
                    effective_autonomy = await self._effective_autonomy_for(
                        identity, task_id=task_id, project_id=task.project
                    )
                loop_mode = (
                    "auto"
                    if self._auto_loop_config is not None
                    else self._loop.get_loop_type()
                )
                logger.info(
                    EXECUTION_ENGINE_START,
                    agent_id=agent_id,
                    task_id=task_id,
                    loop_type=loop_mode,
                    max_turns=max_turns,
                )

                # Stakes-aware routing runs BEFORE the budget block: it
                # sets the target tier from the task's stakes, then the
                # budget auto-downgrade below may lower it further when
                # budget is tight (a hard ceiling must win over a stakes
                # upgrade). When routing picks a model owned by a different
                # provider, the dispatched client is swapped to match so the
                # cost attribution (identity.model.provider) and the API
                # actually called are the same provider.
                if self._stakes_router is not None:
                    routed, reasoning_effort = await self._route_stakes(identity, task)
                    provider, identity = self._resolve_provider_instance(
                        routed,
                        identity,
                        provider,
                    )
                    # Fold the stakes-driven reasoning depth into the run
                    # config so higher-stakes work thinks harder, not only
                    # on a stronger tier. temperature / max_tokens are stable
                    # across the budget downgrade below, so folding here is
                    # safe.
                    completion_config = self._fold_stakes_reasoning(
                        completion_config, identity, reasoning_effort
                    )

                if self._budget_enforcer:
                    preflight = await self._budget_enforcer.check_can_execute(
                        agent_id, provider_name=identity.model.provider
                    )
                    provider, identity = self._apply_degradation(
                        preflight,
                        identity,
                        provider,
                    )
                    pre_downgrade_tier = identity.model.model_tier
                    downgraded = await self._budget_enforcer.resolve_model(identity)
                    if (
                        self._stakes_router is not None
                        and downgraded.model.model_tier != pre_downgrade_tier
                    ):
                        # Budget is a hard ceiling that wins over the stakes
                        # upgrade; record when it clawed a stakes-driven tier back.
                        logger.info(
                            STAKES_ROUTING_BUDGET_OVERRODE,
                            agent_id=agent_id,
                            task_id=str(task.id),
                            stakes_tier=pre_downgrade_tier,
                            downgraded_to=downgraded.model.model_tier,
                        )
                    # resolve_model may downgrade to a model owned by another
                    # provider; re-dispatch and only commit the new identity
                    # once dispatch succeeds, so a registry miss never leaves a
                    # downgraded identity paired with the pre-downgrade client
                    # for the fallback / recovery path to reuse.
                    provider = self._dispatch_client_for(downgraded, provider)
                    identity = downgraded

                # Turn on prompt caching for the run per the operator setting;
                # the driver still gates the actual cache_control placement on
                # per-model caching support. Runs after routing / budget so the
                # final identity's sampling is preserved.
                completion_config = await self._fold_prompt_caching(
                    completion_config, identity
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
                if resume_execution_id is not None and self._event_reader is not None:
                    from synthorg.engine.session import Session  # noqa: PLC0415

                    replay_result = await Session.replay(
                        execution_id=resume_execution_id,
                        event_reader=self._event_reader,
                        identity=identity,
                        task=task,
                        max_turns=max_turns,
                    )
                    if (
                        replay_result.replay_completeness
                        < _REPLAY_LOW_COMPLETENESS_THRESHOLD
                    ):
                        logger.warning(
                            SESSION_REPLAY_LOW_COMPLETENESS,
                            execution_id=resume_execution_id,
                            replay_completeness=replay_result.replay_completeness,
                        )
                    replay_ctx = replay_result.context

                tool_invoker = self._make_tool_invoker(
                    identity,
                    task_id=task_id,
                    effective_autonomy=effective_autonomy,
                    project_id=task.project,
                )
                ctx, system_prompt = await self._prepare_context(
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    max_turns=max_turns,
                    memory_messages=memory_messages,
                    tool_invoker=tool_invoker,
                    effective_autonomy=effective_autonomy,
                )
                if replay_ctx is not None:
                    ctx = ctx.model_copy(
                        update={
                            "execution_id": replay_ctx.execution_id,
                            "started_at": replay_ctx.started_at,
                            "conversation": (
                                *ctx.conversation,
                                *replay_ctx.conversation,
                            ),
                            "accumulated_cost": replay_ctx.accumulated_cost,
                            "turn_count": replay_ctx.turn_count,
                            "task_execution": (
                                replay_ctx.task_execution or ctx.task_execution
                            ),
                        },
                    )
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
                            project_budget=_project_budget,
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
            except ProjectNotFoundError, ProjectAgentNotMemberError:
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
        project_budget = request.project_budget
        with _tracer.start_as_current_span(
            "agent.execution",
            attributes={
                "agent.id": agent_id,
                "task.id": task_id,
                "agent.status": identity.status.value,
            },
        ) as span:
            budget_checker: BudgetChecker | None
            if self._budget_enforcer:
                budget_checker = await self._budget_enforcer.make_budget_checker(
                    await ceiling_synced_task(task, self._cost_forecast_repo),
                    agent_id,
                    project_id=task.project,
                    project_budget=project_budget,
                )
            else:
                budget_checker = make_budget_checker(task)

            logger.debug(
                EXECUTION_ENGINE_PROMPT_BUILT,
                agent_id=agent_id,
                task_id=task_id,
                estimated_tokens=system_prompt.estimated_tokens,
            )

            loop = await self._resolve_loop(task, agent_id, task_id)
            # Project live execution progress onto the AG-UI stream (keyed by
            # session_id == task_id) so the dashboard can render the run
            # instead of a silent gap between propose and review. All
            # best-effort: a missing hub disables projection entirely.
            hub = self._event_stream_hub
            turn_observer = (
                make_turn_observer(hub, task_id=task_id, agent_id=agent_id)
                if hub is not None
                else None
            )
            if hub is not None:
                await publish_run_started(hub, task_id=task_id, agent_id=agent_id)
            # Stream the per-turn LLM calls (with mid-turn cancellation and
            # steer-interrupt) when the operator setting is on and the model
            # supports it; else the loop uses the non-streaming call path.
            streaming_enabled = await self._resolve_streaming_enabled(
                provider or self._provider, identity, task_id=task_id
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
                )

            try:
                execution_result = await _amr.run_with_agent_middleware(
                    self._agent_middleware_chain,
                    loop_runner=_run_loop,
                    ctx=ctx,
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    effective_autonomy=effective_autonomy,
                )
            except Exception as exc:
                # Let a non-recoverable interpreter signal propagate immediately,
                # before the terminal publish below allocates/serialises a frame
                # that could mask the original critical under memory exhaustion.
                reraise_critical(exc)
                # A fatal error skips the normal terminal projection below, so
                # the live panel would hang on "Working" forever: project
                # RUN_ERROR before the exception propagates. A BudgetExhaustedError
                # is excluded -- the outer budget handler converts it to a PARKED
                # approval pause (projected separately) or a BUDGET_EXHAUSTED stop
                # and projects that terminal itself, so a RUN_ERROR here would show
                # a paused run as failed. Cancellation is not caught: a
                # shutdown/disconnect resumes and must not be reported as failed.
                if hub is not None and not isinstance(exc, BudgetExhaustedError):
                    await publish_run_terminated(
                        hub,
                        task_id=task_id,
                        agent_id=agent_id,
                        reason=TerminationReason.ERROR,
                    )
                raise
            if hub is not None:
                await publish_run_terminated(
                    hub,
                    task_id=task_id,
                    agent_id=agent_id,
                    reason=execution_result.termination_reason,
                )

            execution_result = await self._post_execution_pipeline(
                execution_result,
                identity,
                agent_id,
                task_id,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider or self._provider,
                project_id=task.project,
            )

            await self._record_flight_frames(
                execution_result,
                agent_id=agent_id,
                task_id=task_id,
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
            )
