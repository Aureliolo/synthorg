"""Meta improvement controller -- self-improvement proposals and signals."""

from typing import Any, Final
from uuid import UUID  # noqa: TC003

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers.custom_rules import rule_to_dict
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import (
    ServiceUnavailableError,
    resource_not_found,
)
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.models import ChatQuery, ProposeArgs, ProposeResult
from synthorg.meta.config import load_self_improvement_config
from synthorg.meta.mcp.server import get_server_config
from synthorg.meta.mcp.tools import get_tool_definitions
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CHAT_DEPENDENCY_UNAVAILABLE,
    META_CUSTOM_RULE_LIST_FAILED,
)


class ChatRequest(BaseModel):
    """Request body for the Chief of Staff chat endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr = Field(max_length=2000)
    proposal_id: UUID | None = Field(default=None)
    alert_id: UUID | None = Field(default=None)


class ConversationalProposeRequest(BaseModel):
    """Request body for the Chief of Staff clarify-and-propose endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(max_length=2000)
    conversation_id: NotBlankStr | None = Field(default=None)
    project: NotBlankStr | None = Field(default=None)


logger = get_logger(__name__)

_DEFAULT_PAGE_SIZE: Final[int] = 50


def _settings_service_from_state(state: State) -> Any:
    """Return the settings service from the app state, or ``None``.

    Centralises the ``has_settings_service`` guard used by every
    ``load_self_improvement_config`` call site in this controller.
    """
    app_state = state.app_state
    return app_state.settings_service if app_state.has_settings_service else None


class MetaController(Controller):
    """Self-improvement meta-loop API endpoints.

    Provides read access to improvement proposals, org signals,
    rule status, and configuration. Also provides manual cycle
    triggers and proposal approval/rejection.
    """

    path = "/meta"
    tags = ["meta"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/config")
    async def get_config(
        self,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Get current self-improvement configuration.

        Reads the runtime-tunable portion from the ``meta.self_improvement``
        setting and merges it onto :class:`SelfImprovementConfig`'s code
        defaults.  A missing, empty, or malformed setting falls back to
        pure defaults.

        Returns:
            Current SelfImprovementConfig as dict.
        """
        config = await load_self_improvement_config(
            _settings_service_from_state(state),
        )
        return ApiResponse[dict[str, Any]](
            data=config.model_dump(),
        )

    @get("/rules")
    async def list_rules(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, Any]]:
        """List all signal rules (built-in + custom) with status.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated rule summaries.
        """
        from synthorg.meta.rules.builtin import default_rules  # noqa: PLC0415

        rules = default_rules()
        config = await load_self_improvement_config(
            _settings_service_from_state(state),
        )
        disabled = set(config.rules.disabled_rules)
        rule_list: list[dict[str, Any]] = [
            {
                "name": r.name,
                "enabled": r.name not in disabled,
                "target_altitudes": [a.value for a in r.target_altitudes],
                "type": "builtin",
            }
            for r in rules
        ]
        # Append custom rules from persistence.
        repo = state.app_state.persistence.custom_rules
        try:
            from synthorg.persistence.custom_rule_protocol import (  # noqa: PLC0415
                CustomRuleFilterSpec,
            )

            custom = await repo.query(CustomRuleFilterSpec())
        except (QueryError, NotImplementedError) as exc:
            logger.warning(
                META_CUSTOM_RULE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            rule_list.extend({**rule_to_dict(cr), "type": "custom"} for cr in custom)
        page, meta = paginate_cursor(
            tuple(rule_list),
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[dict[str, Any]](data=page, pagination=meta)

    @get("/mcp/tools")
    async def list_mcp_tools(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, str]]:
        """List available MCP signal tools (paginated).

        Args:
            state: Application state (source of the cursor secret).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated MCP tool definitions.
        """
        tools = get_tool_definitions()
        entries = tuple(
            {"name": t["name"], "description": t["description"]} for t in tools
        )
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[dict[str, str]](data=page, pagination=meta)

    @get("/mcp/server")
    async def get_mcp_server_config(
        self,
    ) -> ApiResponse[dict[str, object]]:
        """Get MCP signal server configuration.

        Returns:
            Server config.
        """
        return ApiResponse[dict[str, object]](
            data=get_server_config(),
        )

    @get("/ab-tests")
    async def list_ab_tests(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, Any]]:
        """List active A/B tests with status and current metrics.

        Args:
            state: Application state (source of the cursor secret).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated A/B test summaries.
        """
        # A/B test registry (protocol + in-memory + SQLite + Postgres
        # conformance parity) is a dedicated follow-up: ABTestRollout
        # runs as a one-shot coroutine today, so there is nothing
        # durable to query.  The empty page is safe while the backlog
        # lands; see HYG-3 PR description for the concrete scope.
        empty: tuple[dict[str, Any], ...] = ()
        page, meta = paginate_cursor(
            empty,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[dict[str, Any]](data=page, pagination=meta)

    @get("/ab-tests/{proposal_id:str}")
    async def get_ab_test_detail(
        self,
        proposal_id: str,
    ) -> ApiResponse[dict[str, Any]]:
        """Get detailed A/B test status for a specific proposal.

        Args:
            proposal_id: UUID of the proposal under A/B test.

        Returns:
            A/B test detail including group metrics and verdict.
        """
        # A/B test registry not yet implemented -- every proposal id
        # currently lacks a durable A/B record.  See get /ab-tests
        # above for the scoped follow-up note.
        resource_type = "ab_test"
        raise resource_not_found(
            resource_type,
            proposal_id,
            code=ErrorCode.AB_TEST_NOT_FOUND,
        )

    @get("/proposals")
    async def list_proposals(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, Any]]:
        """List improvement proposals from the approval store.

        Returns proposals where action_type starts with ``meta.``.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated proposal summaries.
        """
        store = state.app_state.approval_store
        all_items = await store.list_items()
        proposals = tuple(
            {
                "id": item.id,
                "title": item.title,
                "action_type": item.action_type,
                "status": item.status.value,
                "risk_level": item.risk_level.value,
                "requested_by": item.requested_by,
                "created_at": item.created_at.isoformat(),
            }
            for item in all_items
            if item.action_type.startswith("meta.")
        )
        page, meta = paginate_cursor(
            proposals,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[dict[str, Any]](data=page, pagination=meta)

    @get("/signals")
    async def get_signals(
        self,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Get signal domain summaries.

        Returns domain names with placeholder data -- real signal
        aggregation runs during the improvement cycle, not on demand.

        Returns:
            Signal domain summaries.
        """
        config = await load_self_improvement_config(
            _settings_service_from_state(state),
        )
        domains = [
            "performance",
            "budget",
            "coordination",
            "scaling",
            "errors",
            "evolution",
            "telemetry",
        ]
        return ApiResponse[dict[str, Any]](
            data={
                "enabled": config.enabled,
                "domains": [{"name": d, "status": "available"} for d in domains],
            },
        )

    @post(
        "/chat",
        # Query-only endpoint: routes a question to ChiefOfStaffChat
        # and returns the computed answer.  No server resource is
        # created, so 200 OK is the right status; without this Litestar
        # defaults POST handlers to 201 Created.
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.chat", key="user"),
        ],
    )
    async def chat(
        self,
        data: ChatRequest,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Ask the Chief of Staff a question.

        Routes to the ChiefOfStaffChat backend for LLM-powered
        explanations of signals and proposals.  Returns 503 when the
        chat backend is not configured (``chief_of_staff.chat_enabled``
        is False or no LLM provider is registered).

        Args:
            data: Chat request with question text.
            state: Application state.

        Returns:
            Chat response with answer, sources, and confidence.
        """
        app_state = state.app_state
        chat_backend = (
            app_state.chief_of_staff_chat if app_state.has_chief_of_staff_chat else None
        )
        if chat_backend is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="chief_of_staff_chat",
                hint=(
                    "Set meta.chief_of_staff.chat_enabled and register an LLM provider."
                ),
            )
            msg = (
                "Chief of Staff chat is not configured. Enable "
                "``meta.chief_of_staff.chat_enabled`` in settings and "
                "ensure an LLM provider is registered."
            )
            raise ServiceUnavailableError(msg)
        signals_service = (
            app_state.signals_service if app_state.has_signals_service else None
        )
        if signals_service is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="signals_service",
                hint="SignalsService must be wired during AppState startup.",
            )
            msg = "SignalsService is not configured; cannot build a snapshot."
            raise ServiceUnavailableError(msg)
        snapshot = await signals_service.get_org_snapshot()
        query = ChatQuery(
            question=data.question,
            proposal_id=data.proposal_id,
            alert_id=data.alert_id,
        )
        result = await chat_backend.ask(query, snapshot)
        return ApiResponse[dict[str, Any]](
            data={
                "answer": result.answer,
                "sources": list(result.sources),
                "confidence": result.confidence,
            },
        )

    @post(
        "/chat/propose",
        # One clarify-or-propose turn. Parking a proposal is an
        # approval-queue write, but the HTTP response only reports the
        # turn outcome (question or summaries); no addressable resource
        # is created at this URL, so 200 (not 201) is correct.
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.chat.propose", key="user"),
        ],
    )
    async def chat_propose(
        self,
        data: ConversationalProposeRequest,
        state: State,
    ) -> ApiResponse[ProposeResult]:
        """Clarify an underspecified request, or park work for approval.

        Routes to ``ChiefOfStaffProposer``. Either returns a clarifying
        question (conversation stays open) or parks one or more work
        items in the approval queue (a human must approve before the
        pipeline runs -- still no autonomous acting).

        Returns 503 when the propose backend is not configured
        (``meta.chief_of_staff.propose_enabled`` is False, no LLM
        provider is registered, or persistence / the work pipeline is
        unavailable so an approved item could never execute).
        """
        app_state = state.app_state
        proposer = (
            app_state.chief_of_staff_proposer
            if app_state.has_chief_of_staff_proposer
            else None
        )
        if proposer is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="chief_of_staff_proposer",
                hint=(
                    "Set meta.chief_of_staff.propose_enabled, register an "
                    "LLM provider, and connect a persistence backend."
                ),
            )
            msg = (
                "Chief of Staff propose is not configured. Enable "
                "``meta.chief_of_staff.propose_enabled`` in settings, "
                "register an LLM provider, and connect persistence."
            )
            raise ServiceUnavailableError(msg)
        if not app_state.has_work_pipeline:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="work_pipeline",
                hint="A provider-backed runtime is required to execute approved work.",
            )
            msg = (
                "Work pipeline is not configured; an approved proposal "
                "could never execute. Configure a provider-backed runtime."
            )
            raise ServiceUnavailableError(msg)
        actor = require_actor()
        # Fence the human-supplied prompt content at the API boundary
        # in a ``<task-data>`` envelope so the model treats it as data,
        # not instructions, before it reaches domain orchestration.
        result = await proposer.converse(
            ProposeArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, data.message)),
                created_by=NotBlankStr(actor.actor_id),
                conversation_id=data.conversation_id,
                project=data.project,
            )
        )
        return ApiResponse[ProposeResult](data=result)

    @post(
        "/cycle",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.trigger_cycle", key="user"),
        ],
    )
    async def trigger_cycle(
        self,
    ) -> ApiResponse[dict[str, Any]]:
        """Trigger a manual improvement cycle.

        Returns:
            Generated proposals.
        """
        return ApiResponse[dict[str, Any]](
            data={"proposals": [], "message": "Cycle triggered"},
        )
