"""Quality domain MCP handlers.

9 tools across quality scores (3), reviews (4), and evaluation-config
version history (2).  All handlers shim through the corresponding
facade on :class:`AppState`; capability gaps surface as typed
``not_supported`` envelopes via :class:`CapabilityNotSupportedError`.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import evaluation_version_service_of
from synthorg.infrastructure.state import (
    quality_facade_service_of,
    review_facade_service_of,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    get_optional_str,
    require_actor_id,
    require_arg,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_OPTIONAL_STRING = "string or null"


def _get_optional_str(arguments: dict[str, Any], key: str) -> str | None:
    """Return ``arguments[key]`` as ``str`` / ``None``, rejecting other types.

    An absent key and an explicit ``null`` are both returned as ``None``.
    A value of any other non-string type raises ``ArgumentValidationError``
    so invalid ``comments`` payloads surface as typed ``invalid_argument``
    envelopes instead of being silently dropped.

    Returns:
        The ``str`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if key not in arguments:
        return None
    raw = arguments[key]
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(key, _TY_OPTIONAL_STRING)
    return raw


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


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = get_optional_str(arguments, key)
    if value is None:
        raise ArgumentValidationError(key, _TY_STRING)
    return value


def _require_uuid(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_UUID) from exc
    return NotBlankStr(value)


def _to_jsonable(value: Any) -> Any:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form.

    Returns:
        ``Any`` instance.
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
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
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
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(summary))


async def _quality_get_agent_quality(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the quality profile for a single agent.

    Returns:
        Resulting string.
    """
    tool = "synthorg_quality_get_agent_quality"
    try:
        agent_id = _require_str(arguments, "agent_id")
        result = await quality_facade_service_of(app_state).get_agent_quality(
            agent_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


async def _quality_list_scores(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List individual quality scores (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_quality_list_scores"
    try:
        offset, limit = coerce_pagination(arguments)
        agent_id = get_optional_str(arguments, "agent_id")
        page, total = await quality_facade_service_of(app_state).list_scores(
            agent_id=agent_id,
            offset=offset,
            limit=limit,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(s) for s in page], pagination=pagination)


# ── reviews ────────────────────────────────────────────────────────


async def _reviews_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List queued review records (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await review_facade_service_of(app_state).list_reviews(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([r.to_dict() for r in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _reviews_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single review by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_get"
    try:
        review_id = _require_uuid(arguments, "review_id")
        record = await review_facade_service_of(app_state).get_review(review_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Review {review_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _reviews_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new review record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_create"
    try:
        task_id = _require_str(arguments, "task_id")
        verdict = _require_str(arguments, "verdict")
        comments = _get_optional_str(arguments, "comments")
        record = await review_facade_service_of(app_state).create_review(
            task_id=task_id,
            reviewer_id=require_actor_id(actor),
            verdict=verdict,
            comments=comments,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _reviews_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update verdict / comments on an existing review.

    Returns:
        Resulting string.
    """
    tool = "synthorg_reviews_update"
    try:
        review_id = _require_uuid(arguments, "review_id")
        verdict = get_optional_str(arguments, "verdict")
        comments = _get_optional_str(arguments, "comments")
        record = await review_facade_service_of(app_state).update_review(
            review_id=review_id,
            verdict=verdict,
            comments=comments,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Review {review_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


# ── evaluation versions ────────────────────────────────────────────


async def _evaluation_versions_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
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
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(v) for v in versions])


async def _evaluation_versions_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single evaluation-config version by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_evaluation_versions_get"
    try:
        version_id = _require_str(arguments, "version_id")
        version = await evaluation_version_service_of(app_state).get_version(
            version_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        return err(
            LookupError(f"Evaluation version {version_id} not found"),
            domain_code="not_found",
        )
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
