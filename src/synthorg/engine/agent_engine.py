"""Agent engine -- top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, TypedDict

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine._validation import (
    validate_agent,
    validate_run_inputs,
    validate_task,
    validate_task_metadata,
)
from synthorg.engine.agent_engine_context import AgentEngineContextMixin
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.agent_engine_factories import AgentEngineFactoriesMixin
from synthorg.engine.agent_engine_post_exec import AgentEnginePostExecMixin
from synthorg.engine.agent_engine_recovery import AgentEngineRecoveryMixin
from synthorg.engine.agent_engine_resume import AgentEngineResumeMixin
from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.context import DEFAULT_MAX_TURNS, AgentContext
from synthorg.engine.errors import (
    ExecutionStateError,
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
)
from synthorg.engine.loop_protocol import make_budget_checker
from synthorg.engine.loop_selector import AutoLoopConfig  # noqa: TC001
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.engine.run_result import AgentRunResult  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.correlation import correlation_scope
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_LOOP_WIRING_WARNING,
)
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
    EXECUTION_PROJECT_VALIDATION_FAILED,
)
from synthorg.observability.events.session import (
    SESSION_REPLAY_LOW_COMPLETENESS,
)
from synthorg.observability.tracing.instrumentation import get_tracer
from synthorg.providers.models import ChatMessage  # noqa: TC001
from synthorg.security.audit import AuditLog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.coordination_collector import CoordinationMetricsCollector
    from synthorg.budget.coordination_config import ErrorTaxonomyConfig
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker import CostTracker
    from synthorg.communication.event_stream.interrupt import InterruptStore
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.compaction import CompactionCallback
    from synthorg.engine.coordination.attribution import (
        CoordinationResultWithAttribution,
    )
    from synthorg.engine.coordination.models import CoordinationContext
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.hybrid_models import HybridLoopConfig
    from synthorg.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
        ShutdownChecker,
    )
    from synthorg.engine.mcp_self_consumer import MCPSelfConsumerProvider
    from synthorg.engine.middleware.protocol import AgentMiddlewareChain
    from synthorg.engine.plan_models import PlanExecuteConfig
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.engine.recovery import RecoveryStrategy
    from synthorg.engine.session import EventReader
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.memory.injection import MemoryInjectionStrategy
    from synthorg.memory.procedural.models import ProceduralMemoryConfig
    from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.ontology.injection.protocol import OntologyInjectionStrategy
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.parked_context_protocol import (
        ParkedContextRepository,
    )
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.providers.routing.resolver import ModelResolver
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.config import SecurityConfig
    from synthorg.security.trust.service import TrustService
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.tools.invocation_tracker import ToolInvocationTracker
    from synthorg.tools.protocol import ToolInvokerProtocol
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
    AgentEngineContextMixin,
    AgentEngineErrorsMixin,
    AgentEngineFactoriesMixin,
    AgentEnginePostExecMixin,
    AgentEngineRecoveryMixin,
    AgentEngineResumeMixin,
):
    """Top-level orchestrator for agent execution."""

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        *,
        provider: CompletionProvider,
        execution_loop: ExecutionLoop | None = None,
        tool_registry: ToolRegistry | None = None,
        cost_tracker: CostTracker | None = None,
        recovery_strategy: RecoveryStrategy | None = _DEFAULT_RECOVERY_STRATEGY,
        shutdown_checker: ShutdownChecker | None = None,
        error_taxonomy_config: ErrorTaxonomyConfig | None = None,
        budget_enforcer: BudgetEnforcer | None = None,
        security_config: SecurityConfig | None = None,
        approval_store: ApprovalStoreProtocol | None = None,
        parked_context_repo: ParkedContextRepository | None = None,
        approval_gate: ApprovalGate | None = None,
        trust_service: TrustService | None = None,
        mcp_self_consumer: MCPSelfConsumerProvider | None = None,
        task_engine: TaskEngine | None = None,
        checkpoint_repo: CheckpointRepository | None = None,
        heartbeat_repo: HeartbeatRepository | None = None,
        checkpoint_config: CheckpointConfig | None = None,
        coordinator: MultiAgentCoordinator | None = None,
        stagnation_detector: StagnationDetector | None = None,
        auto_loop_config: AutoLoopConfig | None = None,
        hybrid_loop_config: HybridLoopConfig | None = None,
        compaction_callback: CompactionCallback | None = None,
        plan_execute_config: PlanExecuteConfig | None = None,
        provider_registry: ProviderRegistry | None = None,
        provider_configs: Mapping[str, ProviderConfig] | None = None,
        model_resolver: ModelResolver | None = None,
        tool_invocation_tracker: ToolInvocationTracker | None = None,
        memory_injection_strategy: MemoryInjectionStrategy | None = None,
        ontology_injection_strategy: OntologyInjectionStrategy | None = None,
        procedural_memory_config: ProceduralMemoryConfig | None = None,
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
        clock: Clock | None = None,
    ) -> None:
        self._agent_middleware_chain = agent_middleware_chain
        self._event_reader = event_reader
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
        self._parked_context_repo = parked_context_repo
        # The boot path constructs one ApprovalGate (backed by the
        # persistence ParkedContextRepository) and injects it so the
        # engine parks and the /approvals controller resumes on the
        # same gate. When absent (standalone / legacy callers) the
        # factory builds a gate from the engine's own collaborators.
        self._injected_approval_gate = approval_gate
        # Progressive trust: when wired, the tool-invoker factory
        # narrows an agent's effective tool access to the more
        # restrictive of its identity level and its earned trust
        # level. ``None`` (trust strategy DISABLED) is a no-op.
        self._trust_service = trust_service
        # Agent -> SynthOrg-MCP self-consumer: when wired, the
        # tool-invoker factory adds trust-scoped SynthOrg MCP tools to
        # the agent's registry. ``None`` (mode DISABLED) is a no-op.
        self._mcp_self_consumer = mcp_self_consumer
        self._approval_interrupt_timeout_seconds = approval_interrupt_timeout_seconds
        self._stagnation_detector = stagnation_detector
        self._auto_loop_config = auto_loop_config
        self._hybrid_loop_config = hybrid_loop_config
        self._compaction_callback = compaction_callback
        self._plan_execute_config = plan_execute_config
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
        self._cost_tracker: CostTracker | None
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
        self._task_engine = task_engine
        self._recovery_strategy = recovery_strategy
        self._shutdown_checker = shutdown_checker
        self._error_taxonomy_config = error_taxonomy_config
        self._coordinator = coordinator
        self._tool_invocation_tracker = tool_invocation_tracker
        self._memory_injection_strategy = memory_injection_strategy
        self._ontology_injection_strategy = ontology_injection_strategy
        self._procedural_memory_config = procedural_memory_config
        self._memory_backend = memory_backend
        self._distillation_capture_enabled = distillation_capture_enabled
        self._config_resolver = config_resolver
        self._personality_trim_notifier = personality_trim_notifier
        self._coordination_metrics_collector = coordination_metrics_collector
        self._procedural_proposer: ProceduralMemoryProposer | None = None
        if (
            procedural_memory_config is not None
            and procedural_memory_config.enabled
            and memory_backend is not None
        ):
            from synthorg.memory.procedural.proposer import (  # noqa: PLC0415
                ProceduralMemoryProposer,
            )

            self._procedural_proposer = ProceduralMemoryProposer(
                provider=provider,
                config=procedural_memory_config,
            )
        self._audit_log = audit_log if audit_log is not None else AuditLog()
        self._project_repo = project_repo
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
            has_plan_execute_config=self._plan_execute_config is not None,
            has_hybrid_loop_config=self._hybrid_loop_config is not None,
            has_personality_trim_notifier=self._personality_trim_notifier is not None,
        )

    @property
    def coordinator(self) -> MultiAgentCoordinator | None:
        """Return the multi-agent coordinator, or ``None`` if not configured."""
        return self._coordinator

    async def coordinate(
        self,
        context: CoordinationContext,
    ) -> CoordinationResultWithAttribution:
        """Delegate to the multi-agent coordinator."""
        if self._coordinator is None:
            msg = "No coordinator configured for multi-agent dispatch"
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                error=msg,
            )
            raise ExecutionStateError(msg)
        return await self._coordinator.coordinate(context)

    async def run(  # noqa: PLR0913, C901
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        completion_config: CompletionConfig | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        memory_messages: tuple[ChatMessage, ...] = (),
        timeout_seconds: float | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        resume_execution_id: str | None = None,
    ) -> AgentRunResult:
        """Execute an agent on a task."""
        agent_id = str(identity.id)
        task_id = task.id

        validate_run_inputs(
            agent_id=agent_id,
            task_id=task_id,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        validate_agent(identity, agent_id)
        validate_task(task, agent_id, task_id)
        validate_task_metadata(task, agent_id, task_id)

        with correlation_scope(agent_id=agent_id, task_id=task_id):
            start = self._clock.monotonic()
            ctx: AgentContext | None = None
            system_prompt: SystemPrompt | None = None
            provider: CompletionProvider = self._provider
            _project_budget: float = 0.0
            try:
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

                if self._budget_enforcer:
                    preflight = await self._budget_enforcer.check_can_execute(
                        agent_id,
                        provider_name=identity.model.provider,
                    )
                    provider, identity = self._apply_degradation(
                        preflight,
                        identity,
                        provider,
                    )
                    identity = await self._budget_enforcer.resolve_model(
                        identity,
                    )

                if self._project_repo is not None:
                    _project_budget = await self._validate_project(
                        task=task,
                        agent_id=agent_id,
                        task_id=task_id,
                    )
                elif task.project:
                    logger.warning(
                        EXECUTION_PROJECT_VALIDATION_FAILED,
                        agent_id=agent_id,
                        task_id=task_id,
                        project_id=task.project,
                        reason="project_repo_not_configured",
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
                return await self._execute(
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
            except MemoryError, RecursionError:
                logger.exception(
                    EXECUTION_ENGINE_ERROR,
                    agent_id=agent_id,
                    task_id=task_id,
                    error="non-recoverable error in run()",
                )
                raise
            except ProjectNotFoundError, ProjectAgentNotMemberError:
                raise
            except BudgetExhaustedError as exc:
                return self._handle_budget_error(
                    exc=exc,
                    identity=identity,
                    task=task,
                    agent_id=agent_id,
                    task_id=task_id,
                    duration_seconds=self._clock.monotonic() - start,
                    ctx=ctx,
                    system_prompt=system_prompt,
                )
            except Exception as exc:
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

    async def _execute(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        ctx: AgentContext,
        system_prompt: SystemPrompt,
        start: float,
        timeout_seconds: float | None = None,
        tool_invoker: ToolInvokerProtocol | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_budget: float = 0.0,
    ) -> AgentRunResult:
        """Run execution loop, record costs, apply transitions, and build result."""
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
                    task,
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

            execution_result = await self._run_loop_with_timeout(
                loop=loop,
                ctx=ctx,
                agent_id=agent_id,
                task_id=task_id,
                completion_config=completion_config,
                budget_checker=budget_checker,
                tool_invoker=tool_invoker,
                start=start,
                timeout_seconds=timeout_seconds,
                provider=provider or self._provider,
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
