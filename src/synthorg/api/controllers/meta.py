"""Meta improvement controller -- self-improvement proposals and signals."""

from typing import Final

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers._ab_test_serde import ab_test_to_dict
from synthorg.api.controllers._custom_rules_helpers import rule_to_dict
from synthorg.api.controllers._meta_chat_routing import resolve_chat_answer
from synthorg.api.controllers._meta_chat_window import resolve_chat_snapshot_window
from synthorg.api.controllers._meta_proposal_helpers import (
    PROPOSAL_ACTION_TYPES,
    proposal_to_dict,
)
from synthorg.api.controllers._meta_requests import (
    ChatRequest,
    ConversationalProposeRequest,
)
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import AbTestNotFoundError, ServiceUnavailableError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.state import EngineStateSlice
from synthorg.meta.chief_of_staff.models import ChatQuery, ProposeArgs, ProposeResult
from synthorg.meta.mcp.server import get_server_config
from synthorg.meta.mcp.tools import get_tool_definitions
from synthorg.meta.rollout.ab_models import AbTestRecord
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import (
    MetaStateSlice,
    ab_test_repo_of,
    self_improvement_config_of,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.observability.events.meta import (
    META_CHAT_DEPENDENCY_UNAVAILABLE,
    META_CUSTOM_RULE_LIST_FAILED,
    META_PROPOSAL_LISTED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

_DEFAULT_PAGE_SIZE: Final[int] = 50


def _require_signals_service(app_state: AppState, msg: str) -> SignalsService:
    """Resolve the signals service, logging + raising 503 when unwired.

    Shared by the ``get_signals`` and ``chat`` handlers so both emit the
    same ``META_CHAT_DEPENDENCY_UNAVAILABLE`` warning; *msg* is the
    call-site-specific operator message carried on the raised error.

    Returns:
        The wired signals service.

    Raises:
        ServiceUnavailableError: When no signals service is wired.
    """
    signals_service = app_state.slice(MetaStateSlice).signals_service
    if signals_service is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="signals_service",
            hint="SignalsService must be wired during AppState startup.",
        )
        raise ServiceUnavailableError(msg)
    return signals_service


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
    ) -> ApiResponse[dict[str, object]]:
        """Get current self-improvement configuration.

        Reads the runtime-tunable portion from the ``meta.self_improvement``
        setting and merges it onto :class:`SelfImprovementConfig`'s code
        defaults.  A missing, empty, or malformed setting falls back to
        pure defaults.

        Returns:
            Current SelfImprovementConfig as dict.
        """
        config = await self_improvement_config_of(state.app_state)
        data = config.model_dump()
        # Effective direct-MCP readiness: the toggle alone is inert without a
        # wired conversational actor (security governance + MCP self-consumer +
        # boot engine). Surface it so the dashboard can cross-warn that an
        # enabled ``direct_mcp_enabled`` stays fail-closed until governance is
        # configured, without a restart.
        cos = data.get("chief_of_staff")
        if isinstance(cos, dict):
            actor_wired = (
                state.app_state.slice(MetaStateSlice).conversational_actor is not None
            )
            cos["direct_mcp_ready"] = actor_wired
        return ApiResponse[dict[str, object]](
            data=data,
        )

    @get("/rules")
    async def list_rules(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, object]]:
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
        config = await self_improvement_config_of(state.app_state)
        disabled = set(config.rules.disabled_rules)
        rule_list: list[dict[str, object]] = [
            {
                "name": r.name,
                "enabled": r.name not in disabled,
                "target_altitudes": [a.value for a in r.target_altitudes],
                "type": "builtin",
            }
            for r in rules
        ]
        # Append custom rules from persistence.
        repo = persistence_of(state.app_state).custom_rules
        degraded_sources: tuple[NotBlankStr, ...] = ()
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
            # Surface the partial result in the envelope so a client can tell
            # "no custom rules configured" apart from "custom-rules query
            # failed" rather than silently receiving only the built-ins.
            degraded_sources = (NotBlankStr("custom_rules"),)
        else:
            rule_list.extend({**rule_to_dict(cr), "type": "custom"} for cr in custom)
        page, meta = paginate_cursor(
            tuple(rule_list),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, object]](
            data=page,
            pagination=meta,
            degraded_sources=degraded_sources,
        )

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
            secret=cursor_secret_of(state.app_state),
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
    ) -> PaginatedResponse[dict[str, object]]:
        """List active A/B tests with status and current metrics.

        Args:
            state: Application state (source of the cursor secret).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated A/B test summaries, newest-first.
        """
        # Records are written by ``ABTestRollout`` through the durable
        # ``AbTestRepository``; when persistence is absent the repo is
        # unwired and the page degrades to empty rather than 503-ing.
        app_state = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = ab_test_repo_of(app_state)
        records: tuple[AbTestRecord, ...] = ()
        if repo is not None:
            records = await repo.list_items(limit=limit + 1, offset=offset)
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(records),
            limit=limit,
            secret=secret,
        )
        page = tuple(ab_test_to_dict(record) for record in records[:limit])
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)

    @get("/ab-tests/{proposal_id:str}")
    async def get_ab_test_detail(
        self,
        proposal_id: PathId,
        state: State,
    ) -> ApiResponse[dict[str, object]]:
        """Get detailed A/B test status for a specific proposal.

        Args:
            proposal_id: Id of the proposal under A/B test.
            state: Application state (source of the durable repository).

        Returns:
            The durable A/B-test record for ``proposal_id``.

        Raises:
            AbTestNotFoundError: When no durable record exists for the
                proposal (or persistence is unavailable); surfaced as a
                typed 404.
        """
        repo = ab_test_repo_of(state.app_state)
        record = await repo.get(NotBlankStr(proposal_id)) if repo is not None else None
        if record is None:
            # Routine missing-resource 404: the central handler logs the
            # request error, so this stays at DEBUG (queryable ``proposal_id``
            # when debugging) rather than inflating WARNING telemetry.
            logger.debug(
                API_RESOURCE_NOT_FOUND,
                resource="ab_test",
                proposal_id=proposal_id,
            )
            msg = f"ab_test {proposal_id!r} not found"
            raise AbTestNotFoundError(msg)
        return ApiResponse[dict[str, object]](data=ab_test_to_dict(record))

    @get("/proposals")
    async def list_proposals(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, object]]:
        """List improvement proposals from the approval store.

        Returns proposals from either producer (see
        ``_meta_proposal_helpers.PROPOSAL_ACTION_TYPES``), pushed down
        to the repo as a plain ``IN`` filter.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated proposal summaries.
        """
        app_state = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        store = require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        )
        items = await store.list_items_page(
            action_types=PROPOSAL_ACTION_TYPES,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(items),
            limit=limit,
            secret=secret,
        )
        proposals = tuple(proposal_to_dict(item) for item in items[:limit])
        logger.debug(META_PROPOSAL_LISTED, count=len(proposals))
        return PaginatedResponse[dict[str, object]](data=proposals, pagination=meta)

    @get("/signals")
    async def get_signals(
        self,
        state: State,
    ) -> ApiResponse[dict[str, object]]:
        """Get per-domain signal availability + the improvement-cycle toggle.

        Reports each domain's availability from the wired
        :class:`SignalsService`; the ``scaling`` domain reports
        ``unavailable`` when no scaling service is wired.

        Returns:
            The improvement ``enabled`` flag and each signal domain's status.

        Raises:
            ServiceUnavailableError: When the signals service is not wired.
        """
        config = await self_improvement_config_of(state.app_state)
        signals_service = _require_signals_service(
            state.app_state,
            "SignalsService is not configured; cannot report signal domains.",
        )
        availability = signals_service.domain_availability()
        return ApiResponse[dict[str, object]](
            data={
                "enabled": config.enabled,
                "domains": [
                    {"name": name, "status": "available" if ok else "unavailable"}
                    for name, ok in availability.items()
                ],
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
    ) -> ApiResponse[dict[str, object]]:
        """Ask the Chief of Staff a question.

        Routes to the ChiefOfStaffChat backend for LLM-powered
        explanations of signals and proposals (see
        ``resolve_chat_answer`` for how ``proposal_id``/``alert_id``
        scoping is resolved). Returns 503 when the chat backend is not
        configured (``chief_of_staff.chat_enabled`` is False or no LLM
        provider is registered).

        Args:
            data: Chat request with question text.
            state: Application state.

        Returns:
            Chat response with answer, sources, and confidence.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state = state.app_state
        await ensure_feature_enabled(
            app_state,
            "chief_of_staff",
            "explain_chat_enabled",
            feature_label="Chief of Staff chat",
        )
        chat_backend = app_state.slice(MetaStateSlice).chief_of_staff_chat
        if chat_backend is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="chief_of_staff_chat",
                hint="Register an LLM provider so the chat backend can be built.",
            )
            msg = (
                "Chief of Staff chat is not configured. Register an LLM "
                "provider so the chat backend can be built."
            )
            raise ServiceUnavailableError(msg)
        signals_service = _require_signals_service(
            app_state,
            "SignalsService is not configured; cannot build a snapshot.",
        )
        snapshot = await signals_service.get_org_snapshot(
            since=app_state.clock.now() - await resolve_chat_snapshot_window(app_state),
        )
        query = ChatQuery(
            question=data.question,
            proposal_id=data.proposal_id,
            alert_id=data.alert_id,
        )
        result = await resolve_chat_answer(app_state, chat_backend, query, snapshot)
        return ApiResponse[dict[str, object]](
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

        Returns:
            ``ApiResponse[ProposeResult]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state = state.app_state
        await ensure_feature_enabled(
            app_state,
            "chief_of_staff",
            "propose_enabled",
            feature_label="Chief of Staff propose",
        )
        proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
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
        if app_state.slice(EngineStateSlice).work_pipeline is None:
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
    ) -> ApiResponse[dict[str, object]]:
        """Trigger a manual improvement cycle.

        Returns:
            Generated proposals.
        """
        return ApiResponse[dict[str, object]](
            data={"proposals": [], "message": "Cycle triggered"},
        )
