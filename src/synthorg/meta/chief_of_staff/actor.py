# module-kind: service
"""Direct MCP acting under trust.

A thin wrapper over :meth:`AgentEngine.run_chat_action`: resolve the
acting agent's identity, resolve its effective autonomy (the same
``AutonomyResolver`` the worker uses), and run the governed taskless
action loop. The actor holds NO governance logic of its own --
escalation, parking, trust narrowing, and the untrusted-content fence
all live in the engine's tool-invoker + ``ApprovalGate`` path, so a
sensitive action escalates and parks exactly as a task action does.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.chat_action import ChatActionResult
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_ACT_COMPLETED,
    COS_ACT_FAILED,
    COS_ACT_PARKED,
    COS_ACT_REQUESTED,
    COS_ACTOR_AGENT_NOT_FOUND,
)
from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)


class ConversationalActArgs(BaseModel):
    """Typed request for a direct chat-driven MCP action."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    instruction: NotBlankStr = Field(description="The human instruction to act on")
    agent: NotBlankStr = Field(
        description="Acting agent identifier or name (resolved via the registry)",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation id for correlation",
    )
    requested_by: str | None = Field(
        default=None,
        description="The human operator who directed the action (audit)",
    )


class ConversationalActResult(BaseModel):
    """Outcome of a direct chat-driven action, with acting-agent attribution."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Id of the agent that acted")
    agent_name: NotBlankStr = Field(description="Name of the agent that acted")
    conversation_id: str | None = Field(
        default=None,
        description="The correlated conversation id, if supplied",
    )
    action: ChatActionResult = Field(description="The engine's chat-action outcome")


class ConversationalActor:
    """Resolve the acting agent + autonomy and run a governed chat action."""

    def __init__(
        self,
        *,
        engine: AgentEngine,
        agent_registry: AgentRegistryService,
        autonomy_resolver: AutonomyResolver | None,
        config: ChiefOfStaffConfig,
    ) -> None:
        self._engine = engine
        self._agent_registry = agent_registry
        self._autonomy_resolver = autonomy_resolver
        self._config = config

    async def act(self, args: ConversationalActArgs) -> ConversationalActResult:
        """Run a direct MCP action for the named agent under its trust.

        Args:
            args: The instruction, acting agent, and optional conversation.

        Returns:
            The action outcome (executed tools + final message, or a
            parked ``approval_id``) with acting-agent attribution.

        Raises:
            NotFoundError: When the named agent is not registered.
        """
        identity = await self._resolve_identity(args.agent)
        agent_id = str(identity.id)
        logger.info(
            COS_ACT_REQUESTED,
            agent_id=agent_id,
            conversation_id=args.conversation_id,
            requested_by=args.requested_by,
        )
        effective_autonomy = self._resolve_autonomy(identity)
        try:
            result = await self._engine.run_chat_action(
                identity=identity,
                instruction=args.instruction,
                effective_autonomy=effective_autonomy,
                max_turns=self._config.direct_mcp_max_turns,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                COS_ACT_FAILED,
                agent_id=agent_id,
                conversation_id=args.conversation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            COS_ACT_PARKED if result.parked else COS_ACT_COMPLETED,
            agent_id=agent_id,
            conversation_id=args.conversation_id,
            termination_reason=result.termination_reason.value,
            approval_id=result.approval_id,
            tool_call_count=len(result.tool_calls),
        )
        return ConversationalActResult(
            agent_id=NotBlankStr(agent_id),
            agent_name=identity.name,
            conversation_id=args.conversation_id,
            action=result,
        )

    async def _resolve_identity(self, agent: str) -> AgentIdentity:
        """Resolve *agent* by id then by name, raising when unknown.

        Returns:
            The resolved :class:`AgentIdentity`.

        Raises:
            NotFoundError: When no agent matches by id or name.
        """
        identity = await self._agent_registry.get(agent)
        if identity is None:
            identity = await self._agent_registry.get_by_name(agent)
        if identity is None:
            msg = "The requested agent is not registered"
            logger.warning(
                COS_ACTOR_AGENT_NOT_FOUND,
                error_type=NotFoundError.__name__,
            )
            raise NotFoundError(msg)
        return identity

    def _resolve_autonomy(
        self,
        identity: AgentIdentity,
    ) -> EffectiveAutonomy | None:
        """Resolve effective autonomy; degrade to ``None`` on misconfig.

        ``None`` still leaves the SecOps rule engine governing every tool
        action; only the autonomy-tier routing layer is skipped.

        Returns:
            The resolved effective autonomy, or ``None`` when no resolver
            is wired or resolution fails (degraded mode).
        """
        if self._autonomy_resolver is None:
            return None
        try:
            return self._autonomy_resolver.resolve(
                agent_level=identity.autonomy_level,
                seniority=identity.level,
            )
        except ValueError as exc:
            logger.warning(
                COS_ACT_FAILED,
                agent_id=str(identity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="autonomy resolution failed -- degrading to rule-engine only",
            )
            return None
