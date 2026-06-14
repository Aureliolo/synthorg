"""Quality domain MCP handlers.

9 tools across quality scores (3), reviews (4), and evaluation-config
version history (2).  All handlers shim through the corresponding
facade on :class:`AppState`; capability gaps surface as typed
``not_supported`` envelopes via :class:`CapabilityNotSupportedError`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.engine.state import evaluation_version_service_of
from synthorg.infrastructure.state import (
    quality_facade_service_of,
    review_facade_service_of,
)
from synthorg.meta.mcp.domains._simple_args import (
    EvaluationVersionsGetArgs,
    QualityGetAgentQualityArgs,
    QualityListScoresArgs,
    ReviewsCreateArgs,
    ReviewsGetArgs,
    ReviewsListArgs,
    ReviewsUpdateArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TY_UUID = "UUID string"
_ARG_REVIEW_ID = "review_id"


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` so capability telemetry is
    distinct from invoke failures.

    Returns:
        Resulting string.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _to_jsonable(value: object) -> object:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form.

    Returns:
        JSON-serialisable representation of ``value``.
    """
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


# ── quality ─────────────────────────────────────────────────────────


async def _quality_get_summary(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the org-wide quality summary.

    Returns:
        Resulting string.
    """
    tool = "synthorg_quality_get_summary"
    try:
        summary = await quality_facade_service_of(app_state).get_summary()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(summary))


async def _quality_get_agent_quality(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the quality profile for a single agent.

    Returns:
        Resulting string.
    """
    tool = "synthorg_quality_get_agent_quality"
    try:
        agent_id = typed_args(arguments, QualityGetAgentQualityArgs).agent_id
        result = await quality_facade_service_of(app_state).get_agent_quality(
            agent_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


async def _quality_list_scores(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List individual quality scores (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_quality_list_scores"
    try:
        args = typed_args(arguments, QualityListScoresArgs)
        offset, limit = args.offset, args.limit
        page, total = await quality_facade_service_of(app_state).list_scores(
            agent_id=args.agent_id,
            offset=offset,
            limit=limit,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(s) for s in page], pagination=pagination)


# ── reviews ────────────────────────────────────────────────────────


async def _reviews_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List queued review records (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_list"
    try:
        page_args = typed_args(arguments, ReviewsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await review_facade_service_of(app_state).list_reviews(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([r.to_dict() for r in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _reviews_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single review by ID.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``review_id`` is not a UUID string.
    """
    tool = "synthorg_reviews_get"
    try:
        review_id = typed_args(arguments, ReviewsGetArgs).review_id
        try:
            UUID(review_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_REVIEW_ID, _TY_UUID) from uuid_exc
        record = await review_facade_service_of(app_state).get_review(review_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Review {review_id} not found")
        log_handler_invoke_failed(tool, missing, review_id=review_id)
        return err(missing, domain_code="not_found")
    return ok(record.to_dict())


async def _reviews_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new review record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_create"
    try:
        args = typed_args(arguments, ReviewsCreateArgs)
        record = await review_facade_service_of(app_state).create_review(
            task_id=args.task_id,
            reviewer_id=require_actor_id(actor),
            verdict=args.verdict,
            comments=args.comments,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _reviews_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update verdict / comments on an existing review.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``review_id`` is not a UUID string.
    """
    tool = "synthorg_reviews_update"
    try:
        args = typed_args(arguments, ReviewsUpdateArgs)
        try:
            UUID(args.review_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_REVIEW_ID, _TY_UUID) from uuid_exc
        record = await review_facade_service_of(app_state).update_review(
            review_id=args.review_id,
            verdict=args.verdict,
            comments=args.comments,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Review {args.review_id} not found")
        log_handler_invoke_failed(tool, missing, review_id=args.review_id)
        return err(missing, domain_code="not_found")
    return ok(record.to_dict())


# ── evaluation versions ────────────────────────────────────────────


async def _evaluation_versions_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List evaluation-config version snapshots.

    Returns:
        Resulting string.
    """
    tool = "synthorg_evaluation_versions_list"
    try:
        versions = await evaluation_version_service_of(app_state).list_versions()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(v) for v in versions])


async def _evaluation_versions_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single evaluation-config version by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_evaluation_versions_get"
    try:
        version_id = typed_args(arguments, EvaluationVersionsGetArgs).version_id
        version = await evaluation_version_service_of(app_state).get_version(
            version_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        missing = NotFoundError(f"Evaluation version {version_id} not found")
        log_handler_invoke_failed(tool, missing, version_id=version_id)
        return err(missing, domain_code="not_found")
    return ok(_to_jsonable(version))


QUALITY_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_quality_get_summary": _quality_get_summary,
        "synthorg_quality_get_agent_quality": _quality_get_agent_quality,
        "synthorg_quality_list_scores": _quality_list_scores,
        "synthorg_reviews_list": _reviews_list,
        "synthorg_reviews_get": _reviews_get,
        "synthorg_reviews_create": _reviews_create,
        "synthorg_reviews_update": _reviews_update,
        "synthorg_evaluation_versions_list": _evaluation_versions_list,
        "synthorg_evaluation_versions_get": _evaluation_versions_get,
    },
)
