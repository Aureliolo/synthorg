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

import re
from typing import Final, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.chat_action import ChatActionResult
from synthorg.engine.context import AgentContext
from synthorg.integrations.connections.secret_capture import (
    PendingSecretCapture,
    SecretCaptureService,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.console_conversation_store import (
    ConsoleConversationStore,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONSOLE_AUTONOMY_DEGRADED,
    COS_CONSOLE_COMPLETED,
    COS_CONSOLE_FAILED,
    COS_CONSOLE_PARKED,
    COS_CONSOLE_REQUESTED,
)
from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)

# Every value interpolated into the trusted capture brief is a server-issued
# opaque token, never free text: a draft id the console minted (``draft-<uuid>``),
# a capture handle from ``SecretCaptureService`` (``sech_<token>``), or a
# connection field name. Validating each to its exact shape at the args boundary
# keeps request-controlled data out of the system prompt (no injection, no
# unbounded growth) without needing ``wrap_untrusted`` in the prompt itself.
_DRAFT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^draft-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HANDLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sech_[A-Za-z0-9_-]{16,128}$")
_FIELD_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_]{1,64}$")
_MAX_PROVIDED_HANDLES: Final[int] = 16

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


def _capture_brief(
    draft_id: str,
    provided_handles: dict[NotBlankStr, NotBlankStr],
) -> str:
    """Build the per-turn secret-capture guidance for the console prompt.

    Tells the console the draft id to bind captures to, how to ask for a secret
    out of band (``connections.request_secret_capture``), and surfaces any
    handles the operator already provided so the console passes them to
    ``connections.create``. Handles are opaque single-use references, not the
    secret value, so they are safe to place in the prompt.

    Returns:
        The trusted guidance string to append to the operating brief.
    """
    brief = (
        f"\n\nThis setup session's connection draft id is '{draft_id}'. When you "
        "need a secret field (a token, password, or key) to create a connection, "
        "call connections.request_secret_capture with this draft id and the field "
        "name instead of asking for the value in chat: the operator provides it "
        "out of band and you receive an opaque handle on a later turn. Pass "
        "captured handles to connections.create as credential_handles with "
        f"connection_draft_id set to '{draft_id}'."
    )
    if not provided_handles:
        return brief
    pairs = ", ".join(f"{field}={handle}" for field, handle in provided_handles.items())
    return (
        brief + " The operator has now provided these capture handles (opaque "
        f"references, not secrets): {pairs}. Use them as credential_handles on "
        "connections.create for this draft."
    )


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
    connection_draft_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Setup draft id to continue; None starts a fresh draft. The console "
            "binds every secret-capture request and the resulting connections."
            "create to this id."
        ),
    )
    provided_credential_handles: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description=(
            "Opaque single-use capture handles the operator supplied out of band "
            "since the last turn, as field-name -> handle. Injected into the "
            "console's context so it passes them to connections.create; the raw "
            "secret is never here."
        ),
    )

    @model_validator(mode="after")
    def _validate_capture_tokens(self) -> Self:
        """Reject any capture token that is not a server-issued opaque value.

        ``connection_draft_id`` and every ``provided_credential_handles`` field
        name / handle is interpolated into the console's trusted system prompt,
        so each must match the exact shape the server mints. A malformed value
        (a would-be prompt-injection payload, or an oversized map) is rejected
        here rather than reaching the prompt.

        Returns:
            ``self`` when every capture token is well-formed.

        Raises:
            ValueError: If the draft id, a handle, or a field name is malformed,
                or too many handles are supplied.
        """
        if self.connection_draft_id is not None and not _DRAFT_ID_PATTERN.fullmatch(
            self.connection_draft_id
        ):
            msg = "connection_draft_id must be a server-issued 'draft-<uuid>' token"
            raise ValueError(msg)
        if len(self.provided_credential_handles) > _MAX_PROVIDED_HANDLES:
            msg = f"at most {_MAX_PROVIDED_HANDLES} credential handles per turn"
            raise ValueError(msg)
        for field_name, handle in self.provided_credential_handles.items():
            if not _FIELD_NAME_PATTERN.fullmatch(field_name):
                msg = f"credential handle field name {field_name!r} is malformed"
                raise ValueError(msg)
            if not _HANDLE_PATTERN.fullmatch(handle):
                msg = f"credential handle for {field_name!r} is not a valid handle"
                raise ValueError(msg)
        return self


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
    connection_draft_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "The setup draft id this turn used; echo it back with captured "
            "handles on the next turn to continue the flow."
        ),
    )
    pending_captures: tuple[PendingSecretCapture, ...] = Field(
        default=(),
        description=(
            "Secret fields the console asked the operator to provide out of band "
            "this turn; the dashboard renders a masked input per entry."
        ),
    )


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
        secret_capture: The out-of-band secret-capture service, or ``None`` when
            integrations are disabled. Read after each turn for the masked
            fields the console asked for via ``connections.request_secret_capture``.
        conversations: Process-local store of per-conversation console context,
            or ``None`` to run each turn cold. When wired, a turn carrying a
            ``conversation_id`` continues the prior conversation (its setup
            memory) instead of losing it.
    """

    def __init__(  # noqa: PLR0913 -- injected console dependencies
        self,
        *,
        engine: AgentEngine,
        identity: AgentIdentity,
        autonomy_resolver: AutonomyResolver | None,
        config: ChiefOfStaffConfig,
        secret_capture: SecretCaptureService | None = None,
        conversations: ConsoleConversationStore | None = None,
    ) -> None:
        self._engine = engine
        self._identity = identity
        self._autonomy_resolver = autonomy_resolver
        self._config = config
        self._secret_capture = secret_capture
        self._conversations = conversations

    async def configure(self, args: ConsoleTurnArgs) -> ConsoleTurnResult:
        """Run one governed configure turn as the console identity.

        Args:
            args: The instruction and optional conversation correlation.

        Returns:
            The configure outcome (executed tools + final message, or a
            parked ``approval_id``) with console attribution.
        """
        console_id = str(self._identity.id)
        draft_id = args.connection_draft_id or NotBlankStr(f"draft-{uuid4()}")
        logger.info(
            COS_CONSOLE_REQUESTED,
            console_id=console_id,
            conversation_id=args.conversation_id,
            requested_by=args.requested_by,
        )
        effective_autonomy = self._resolve_autonomy()
        prior_context = self._load_prior_context(args.conversation_id)
        try:
            result, final_context = await self._engine.run_chat_action_session(
                identity=self._identity,
                instruction=args.instruction,
                effective_autonomy=effective_autonomy,
                max_turns=self._config.operator_console_max_turns,
                cost_ceiling=self._config.operator_console_cost_ceiling,
                system_prompt_addendum=CONSOLE_OPERATING_BRIEF
                + _capture_brief(draft_id, args.provided_credential_handles),
                prior_context=prior_context,
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
        self._save_context(args.conversation_id, final_context, parked=result.parked)
        pending = (
            self._secret_capture.take_pending(draft_id)
            if self._secret_capture is not None
            else ()
        )
        logger.info(
            COS_CONSOLE_PARKED if result.parked else COS_CONSOLE_COMPLETED,
            console_id=console_id,
            conversation_id=args.conversation_id,
            termination_reason=result.termination_reason.value,
            approval_id=result.approval_id,
            tool_call_count=len(result.tool_calls),
            pending_capture_count=len(pending),
        )
        return ConsoleTurnResult(
            console_id=NotBlankStr(console_id),
            console_name=self._identity.name,
            conversation_id=args.conversation_id,
            action=result,
            connection_draft_id=draft_id,
            pending_captures=pending,
        )

    def _load_prior_context(
        self, conversation_id: NotBlankStr | None
    ) -> AgentContext | None:
        """Load the stored context for a conversation to continue it.

        Returns:
            The prior :class:`AgentContext` when a store is wired and the
            conversation has one, else ``None`` (the turn runs cold).
        """
        if self._conversations is None or conversation_id is None:
            return None
        return self._conversations.load(conversation_id)

    def _save_context(
        self,
        conversation_id: NotBlankStr | None,
        context: AgentContext,
        *,
        parked: bool,
    ) -> None:
        """Persist the turn's resulting context so the next turn continues it.

        Skipped for a parked turn: a parked action is mid-flight and resumes
        through the approval gate, so its context is owned by the park store,
        not this per-conversation memory.
        """
        if self._conversations is None or conversation_id is None or parked:
            return
        self._conversations.save(conversation_id, context)

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
            logger.error(
                COS_CONSOLE_AUTONOMY_DEGRADED,
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
