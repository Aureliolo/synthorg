"""Knowledge-substrate domain MCP handlers.

Delegates to :class:`KnowledgeService` via ``app_state.knowledge_service``.
Search / list / get are read-only; ingest / reindex / delete are admin
(call ``require_admin_guardrails`` as the first body statement). Search
chunk text and synthesised answer + claim texts are wrapped via
``wrap_untrusted`` because corpus content may carry injected instructions.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.prompt_safety import (
    TAG_KNOWLEDGE,
    TAG_MEMORY_ENTRY,
    wrap_untrusted,
)
from synthorg.knowledge.errors import KnowledgeSourceNotFoundError
from synthorg.knowledge.models import KnowledgeAnswer, KnowledgeHit
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.meta.mcp.domains._knowledge_args import (
    KnowledgeAskArgs,
    KnowledgeDeleteArgs,
    KnowledgeGetArgs,
    KnowledgeIngestArgs,
    KnowledgeListArgs,
    KnowledgeReindexArgs,
    KnowledgeSearchArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_admin_op_executed,
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOL_SEARCH = "synthorg_knowledge_search"
_TOOL_ASK = "synthorg_knowledge_ask"
_TOOL_INGEST = "synthorg_knowledge_ingest"
_TOOL_REINDEX = "synthorg_knowledge_reindex"
_TOOL_LIST = "synthorg_knowledge_list"
_TOOL_GET = "synthorg_knowledge_get"
_TOOL_DELETE = "synthorg_knowledge_delete"


def _require_service(app_state: AppState) -> KnowledgeService:
    """Return the service or raise when unavailable.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc = app_state.slice(KnowledgeStateSlice).service
    if svc is None:
        msg = "knowledge service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


async def _require_enabled_service(
    app_state: AppState,
    *,
    require_synthesis: bool = False,
) -> KnowledgeService:
    """Live-gate the knowledge substrate, then return the wired service.

    The substrate is ghost-wired at startup (built whenever persistence + a
    memory backend exist), so the ``knowledge.enabled`` master switch is
    enforced here per request: toggling it in settings takes effect on the
    next call with no restart. The grounded-answer ``/ask`` capability is
    additionally gated on ``knowledge.synthesis_enabled`` when
    *require_synthesis* is set, so disabling synthesis 503s ``ask`` while
    search / ingest / list / get / reindex / delete stay live.

    Args:
        app_state: The application state (carries the config resolver + slice).
        require_synthesis: Also require ``knowledge.synthesis_enabled``.

    Returns:
        The wired knowledge service when the gates pass.

    Raises:
        ServiceUnavailableError: When a gated flag resolves to ``False`` or the
            service is not wired.
    """
    from synthorg.api._feature_gate import ensure_feature_enabled  # noqa: PLC0415

    await ensure_feature_enabled(
        app_state, "knowledge", "enabled", feature_label="Knowledge"
    )
    if require_synthesis:
        await ensure_feature_enabled(
            app_state,
            "knowledge",
            "synthesis_enabled",
            feature_label="Knowledge answer synthesis",
        )
    return _require_service(app_state)


def _hit_dict(hit: KnowledgeHit) -> dict[str, object]:
    """Serialise a hit, wrapping the untrusted chunk text.

    Returns:
        Mapping with the declared key/value types.
    """
    return {
        "chunk_text": wrap_untrusted(TAG_MEMORY_ENTRY, hit.chunk_text),
        "relevance_score": hit.relevance_score,
        "citation": hit.citation.model_dump(mode="json"),
    }


def _answer_dict(answer: KnowledgeAnswer) -> dict[str, object]:
    """Serialise an answer, fencing the synthesised prose + claim texts.

    The answer and claim texts are model output synthesised from untrusted
    corpus chunks, so they are wrapped via ``wrap_untrusted`` (mirroring
    :func:`_hit_dict`) before crossing the MCP boundary.

    Returns:
        Mapping with the declared key/value types.
    """
    dumped = answer.model_dump(mode="json")
    dumped["answer"] = wrap_untrusted(TAG_KNOWLEDGE, answer.answer)
    dumped["claims"] = [
        {**claim_dump, "text": wrap_untrusted(TAG_KNOWLEDGE, claim.text)}
        for claim_dump, claim in zip(dumped["claims"], answer.claims, strict=True)
    ]
    return dumped


async def _knowledge_search(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return knowledge search."""
    try:
        svc = await _require_enabled_service(app_state)
        search_args = typed_args(arguments, KnowledgeSearchArgs)
        hits = await svc.search(
            query=search_args.query,
            project_id=search_args.project_id,
            limit=search_args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_SEARCH)
        return ok([_hit_dict(h) for h in hits])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_SEARCH, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_SEARCH, exc)
        return err(exc)


async def _knowledge_ask(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a grounded, citation-bound answer over the corpus."""
    try:
        svc = await _require_enabled_service(app_state, require_synthesis=True)
        ask_args = typed_args(arguments, KnowledgeAskArgs)
        answer = await svc.ask(
            query=ask_args.query,
            project_id=ask_args.project_id,
            limit=ask_args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_ASK)
        return ok(_answer_dict(answer))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_ASK, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_ASK, exc)
        return err(exc)


async def _knowledge_ingest(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return knowledge ingest."""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        svc = await _require_enabled_service(app_state)
        args = typed_args(arguments, KnowledgeIngestArgs)
        source = await svc.ingest(
            source_type=args.source_type,
            uri=args.uri,
            title=args.title,
            project_id=args.project_id,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_INGEST)
        log_handler_admin_op_executed(
            _TOOL_INGEST,
            reason=reason,
            actor=resolved_actor,
            target_id=source.source_id,
        )
        return ok(source.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(_TOOL_INGEST, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_INGEST, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_INGEST, exc)
        return err(exc)


async def _knowledge_reindex(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return knowledge reindex."""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        svc = await _require_enabled_service(app_state)
        source_id = typed_args(arguments, KnowledgeReindexArgs).source_id
        source = await svc.reindex(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_REINDEX)
        log_handler_admin_op_executed(
            _TOOL_REINDEX,
            reason=reason,
            actor=resolved_actor,
            target_id=source.source_id,
        )
        return ok(source.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(_TOOL_REINDEX, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_REINDEX, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_REINDEX, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_REINDEX, exc)
        return err(exc)


async def _knowledge_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return knowledge list."""
    try:
        svc = await _require_enabled_service(app_state)
        list_args = typed_args(arguments, KnowledgeListArgs)
        sources = await svc.list_sources(
            project_id=list_args.project_id,
            include_global=list_args.include_global,
            stale_only=list_args.stale_only,
            limit=list_args.limit,
            offset=list_args.offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([s.model_dump(mode="json") for s in sources])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_LIST, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


async def _knowledge_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return knowledge get."""
    try:
        svc = await _require_enabled_service(app_state)
        source_id = typed_args(arguments, KnowledgeGetArgs).source_id
        source = await svc.get_source(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(source.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_GET, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _knowledge_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return knowledge delete."""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        svc = await _require_enabled_service(app_state)
        source_id = typed_args(arguments, KnowledgeDeleteArgs).source_id
        deleted = await svc.delete_source(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DELETE)
        log_handler_admin_op_executed(
            _TOOL_DELETE,
            reason=reason,
            actor=resolved_actor,
            target_id=source_id,
        )
        return ok({"source_id": source_id, "deleted": deleted})
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(_TOOL_DELETE, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DELETE, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DELETE, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DELETE, exc)
        return err(exc)


KNOWLEDGE_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_knowledge_search": _knowledge_search,
        "synthorg_knowledge_ask": _knowledge_ask,
        "synthorg_knowledge_ingest": _knowledge_ingest,
        "synthorg_knowledge_reindex": _knowledge_reindex,
        "synthorg_knowledge_list": _knowledge_list,
        "synthorg_knowledge_get": _knowledge_get,
        "synthorg_knowledge_delete": _knowledge_delete,
    },
)
