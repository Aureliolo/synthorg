# module-kind: controller
"""Conversational write-path controller: multi-agent group chat.

Kept separate from :class:`MetaController` so the conversational
write-path endpoints (the direct-MCP ``/act`` endpoint joins here for
#1972) grow cohesively without pushing the meta controller past its
size tier. Mounted under ``/meta/chat`` alongside the explain-only and
clarify-and-propose endpoints that live on ``MetaController``.
"""

from litestar import Controller, post
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.actor import (
    ConversationalActArgs,
    ConversationalActResult,
)
from synthorg.meta.chief_of_staff.group_models import (
    GroupConverseArgs,
    GroupConverseResult,
)
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE

logger = get_logger(__name__)


class GroupChatRequest(BaseModel):
    """Request body for the multi-agent group-chat endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(max_length=2000)
    conversation_id: NotBlankStr | None = Field(default=None)
    participants: tuple[NotBlankStr, ...] = Field(default=())


class ChatActRequest(BaseModel):
    """Request body for the direct-MCP acting endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    instruction: NotBlankStr = Field(max_length=2000)
    agent: NotBlankStr = Field(max_length=200)
    conversation_id: NotBlankStr | None = Field(default=None)


class ConversationalController(Controller):
    """Multi-agent conversational write-path API endpoints."""

    path = "/meta/chat"
    tags = ["meta"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/group",
        # One round-robin round. Appends turns and may enrol
        # participants, but no addressable resource is created at this
        # URL, so 200 (not 201) is correct.
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.chat.group", key="user"),
        ],
    )
    async def chat_group(
        self,
        data: GroupChatRequest,
        state: State,
    ) -> ApiResponse[GroupConverseResult]:
        """Run one round-robin round across the group's active agents.

        Opens a new ``kind='group'`` conversation (naming the initial
        participants) or continues an existing one. Each active
        participant contributes once, in enrolment order, seeing the
        shared transcript; contributions are attributed and persisted.

        Returns 503 when the group chat backend is not configured
        (``meta.chief_of_staff.group_chat_enabled`` is False, no LLM
        provider / agent registry, or persistence is unavailable).

        Returns:
            ``ApiResponse[GroupConverseResult]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state = state.app_state
        service = app_state.slice(MetaStateSlice).group_chat_service
        if service is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="group_chat_service",
                hint=(
                    "Set meta.chief_of_staff.group_chat_enabled, register an "
                    "LLM provider, configure agents, and connect persistence."
                ),
            )
            msg = (
                "Group chat is not configured. Enable "
                "``meta.chief_of_staff.group_chat_enabled`` in settings, "
                "register an LLM provider, configure agents, and connect "
                "persistence."
            )
            raise ServiceUnavailableError(msg)
        actor = require_actor()
        # Fence the human-supplied message at the API boundary in a
        # ``<task-data>`` envelope so the model treats it as data, not
        # instructions, before it reaches the round loop.
        result = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, data.message)),
                created_by=NotBlankStr(actor.actor_id),
                conversation_id=data.conversation_id,
                participants=data.participants,
            )
        )
        return ApiResponse[GroupConverseResult](data=result)

    @post(
        "/act",
        # Runs a short tool-capable action loop and may append to the
        # approval queue, but creates no addressable resource at this
        # URL, so 200 (not 201) is correct.
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.chat.act", key="user"),
        ],
    )
    async def chat_act(
        self,
        data: ChatActRequest,
        state: State,
    ) -> ApiResponse[ConversationalActResult]:
        """Drive a real MCP action from a chat instruction under trust.

        The named agent runs a short governed tool loop: a permitted
        action executes under its trust level; a sensitive action
        escalates and parks to the approval queue (the response then
        carries the parked ``approval_id`` and the agent resumes on
        approval via the existing Flow 1).

        Returns 503 when the actor is not configured
        (``meta.chief_of_staff.direct_mcp_enabled`` is False, no
        provider-backed boot engine, or no MCP self-consumer wired).

        The instruction is fenced (``<task-data>``) inside
        ``run_chat_action`` itself, so -- unlike the group endpoint --
        the controller passes it through raw to avoid a double fence.

        Returns:
            ``ApiResponse[ConversationalActResult]`` instance.

        Raises:
            ServiceUnavailableError: When the actor is not configured.
        """
        app_state = state.app_state
        actor_service = app_state.slice(MetaStateSlice).conversational_actor
        if actor_service is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="conversational_actor",
                hint=(
                    "Set meta.chief_of_staff.direct_mcp_enabled, register an "
                    "LLM provider, and enable the MCP self-consumer "
                    "(security.mcp_self_consumer.mode=trust_scoped)."
                ),
            )
            msg = (
                "Direct MCP acting is not configured. Enable "
                "``meta.chief_of_staff.direct_mcp_enabled`` in settings, "
                "register an LLM provider, and set "
                "``security.mcp_self_consumer.mode`` to ``trust_scoped``."
            )
            raise ServiceUnavailableError(msg)
        operator = require_actor()
        result = await actor_service.act(
            ConversationalActArgs(
                instruction=data.instruction,
                agent=data.agent,
                conversation_id=data.conversation_id,
                requested_by=operator.actor_id,
            )
        )
        return ApiResponse[ConversationalActResult](data=result)
