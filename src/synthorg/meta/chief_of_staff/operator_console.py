# module-kind: service
"""Operator console: configure the control plane from the unified chat.

A CONFIGURE turn drives the control plane (connect an integration, change a
setting, install a catalogue entry, call any control-plane tool) as a shared
system ``console`` identity. Like :class:`ConversationalActor` this is a thin
wrapper over :meth:`AgentEngine.run_chat_action`: the console holds NO
governance logic of its own. Every tool call flows through the same
``ToolInvoker`` -> SecOps pipeline and the shared ``ApprovalGate``, so a
sensitive configure step escalates and parks exactly as a task action does.

The console differs from the direct-MCP actor in three ways: it acts as one
fixed ELEVATED system identity (never a business agent), it is gated by its
own default-off ``operator_console_enabled`` toggle, and it bounds each
session by a hard cost ceiling in addition to the turn cap.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.chat_action import ChatActionResult
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import BudgetChecker
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONSOLE_COMPLETED,
    COS_CONSOLE_FAILED,
    COS_CONSOLE_PARKED,
    COS_CONSOLE_REQUESTED,
)
from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)

CONSOLE_OPERATING_BRIEF: str = (
    "You are the operator console for this synthetic-organisation platform: "
    "the operator's cockpit over the control plane, not one of the org's "
    "working agents. Use the available control-plane tools to configure, "
    "connect, and administer the platform on the operator's behalf: connect "
    "integrations, install catalogue entries, adjust settings, and report "
    "state. Read the current state before you change it, and take the "
    "smallest step that satisfies the request. When a change is risky or "
    "destructive the platform will require an approval: proceed and let it "
    "park rather than refusing, and tell the operator it is awaiting their "
    "approval. Never ask for, echo, or repeat a secret value (a token, "
    "password, or key) in plain text: secrets are captured out of band "
    "through a masked field and referenced only by an opaque handle. Confirm "
    "what you did, or what is pending, concisely."
)
"""Trusted operating brief appended to the console identity's persona prompt."""


class ConsoleTurnArgs(BaseModel):
    """Typed request for one operator-console configure turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    instruction: NotBlankStr = Field(
        description="The operator instruction to configure/operate the platform"
    )
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="Optional conversation id for correlation",
    )
    requested_by: NotBlankStr | None = Field(
        default=None,
        description="The authenticated operator who directed the turn (audit)",
    )


class ConsoleTurnResult(BaseModel):
    """Outcome of one operator-console configure turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    console_id: NotBlankStr = Field(description="Id of the system console identity")
    console_name: NotBlankStr = Field(description="Name of the console identity")
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="The correlated conversation id, if supplied",
    )
    action: ChatActionResult = Field(description="The engine's chat-action outcome")


class OperatorConsoleService:
    """Run a governed configure loop as the shared system console identity.

    Args:
        engine: The shared boot :class:`AgentEngine` (so a parked configure
            step resumes on the same ``ApprovalGate`` the ``/approvals``
            controller drives).
        identity: The pre-built ELEVATED system ``console`` identity.
        autonomy_resolver: Resolver for the console's effective autonomy, or
            ``None`` to leave the rule engine governing without the
            autonomy-tier layer.
        config: Chief of Staff configuration (turn cap + cost ceiling).
    """

    def __init__(
        self,
        *,
        engine: AgentEngine,
        identity: AgentIdentity,
        autonomy_resolver: AutonomyResolver | None,
        config: ChiefOfStaffConfig,
    ) -> None:
        self._engine = engine
        self._identity = identity
        self._autonomy_resolver = autonomy_resolver
        self._config = config

    async def configure(self, args: ConsoleTurnArgs) -> ConsoleTurnResult:
        """Run one governed configure turn as the console identity.

        Args:
            args: The instruction and optional conversation correlation.

        Returns:
            The configure outcome (executed tools + final message, or a
            parked ``approval_id``) with console attribution.
        """
        console_id = str(self._identity.id)
        logger.info(
            COS_CONSOLE_REQUESTED,
            console_id=console_id,
            conversation_id=args.conversation_id,
            requested_by=args.requested_by,
        )
        effective_autonomy = self._resolve_autonomy()
        try:
            result = await self._engine.run_chat_action(
                identity=self._identity,
                instruction=args.instruction,
                effective_autonomy=effective_autonomy,
                max_turns=self._config.operator_console_max_turns,
                budget_checker=self._build_budget_checker(),
                system_prompt_addendum=CONSOLE_OPERATING_BRIEF,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                COS_CONSOLE_FAILED,
                console_id=console_id,
                conversation_id=args.conversation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            COS_CONSOLE_PARKED if result.parked else COS_CONSOLE_COMPLETED,
            console_id=console_id,
            conversation_id=args.conversation_id,
            termination_reason=result.termination_reason.value,
            approval_id=result.approval_id,
            tool_call_count=len(result.tool_calls),
        )
        return ConsoleTurnResult(
            console_id=NotBlankStr(console_id),
            console_name=self._identity.name,
            conversation_id=args.conversation_id,
            action=result,
        )

    def _build_budget_checker(self) -> BudgetChecker:
        """Build a per-session budget checker closing over the cost ceiling.

        Returns:
            A callback that trips once accumulated session cost meets or
            exceeds ``operator_console_cost_ceiling_usd``.
        """
        ceiling = self._config.operator_console_cost_ceiling_usd

        def _check(ctx: AgentContext) -> bool:
            return ctx.accumulated_cost.cost >= ceiling

        return _check

    def _resolve_autonomy(self) -> EffectiveAutonomy | None:
        """Resolve the console's effective autonomy; degrade on misconfig.

        ``None`` still leaves the SecOps rule engine governing every tool
        action; only the autonomy-tier routing layer is skipped.

        Returns:
            The resolved effective autonomy, or ``None`` when no resolver is
            wired or resolution fails (degraded mode).
        """
        if self._autonomy_resolver is None:
            return None
        try:
            return self._autonomy_resolver.resolve(
                agent_level=self._identity.autonomy_level,
            )
        except ValueError as exc:
            logger.warning(
                COS_CONSOLE_FAILED,
                console_id=str(self._identity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="autonomy resolution failed -- degrading to rule-engine only",
            )
            return None


__all__ = [
    "ConsoleTurnArgs",
    "ConsoleTurnResult",
    "OperatorConsoleService",
]
